"""
Premieres scraper v6 — per-publisher binary search + linear scan.

History:
  v1/v2: Catalog publishedMonth=4 → showed wrong month (0-indexed)
  v3:    Publisher pages /wydawnictwo/{id}/{slug}/ksiazki → ignored all params
  v4:    All-publisher catalog → wrong IDs (0 results)
  v5:    Correct IDs; per-publisher catalog; 3 bugs remained:
           (a) AJAX params (listId/listType/paginatorType) may cause AJAX fragment
               response with different HTML structure → stubs=[] → publisher skipped
           (b) year-only dates: _parse_published_date returns date(year,1,1) for
               "rok wydania: 2026"; probe gets (2026,1) < (2026,4) target → skip
           (c) no fallback when publishedYear filter returns 0 results
  v6:    Fixes all three:
           (a) removed AJAX params from URL; fallback URL without publishedYear
           (b) _is_year_only() + probe retries up to 3 books; linear scan treats
               year-only-same-year as "skip" (not "stop")
           (c) if year-filtered URL returns 0 stubs, retry without year filter

Algorithm per publisher:
  1. Probe page 1: fetch listing + first book detail page → get exact date.
     Year-only dates (month=1, day=1) retried on next book; treated as (year,12)
     if unavoidable (conservative = "could be any month, treat as future").
  2. date < target (and not year-only from target year) → skip publisher.
     date == target → scan from page 1.
     date > target → binary search for the transition page.
  3. Scan from max(1, transition_page - 1).
  4. Linear scan: skip future, collect exact-match, skip year-only-ambiguous,
     stop at definite past.
"""

import logging
import re
import unicodedata
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from .models import PremiereBook
from .config import BASE_URL
from .scraper import (
    _make_session,
    _fetch,
    _sleep,
    _parse_listing_page,
    _parse_published_date,
    extract_book_id,
)

logger = logging.getLogger(__name__)

# ── Publisher registry ────────────────────────────────────────────────────────
# (display_name, lubimyczytac_id, url_slug)
# IDs extracted from user-provided lubimyczytac.pl publisher URLs.

KNOWN_PUBLISHERS: list[tuple[str, int, str]] = [
    ("Znak",                     10760, "znak"),
    ("Znak Koncept",             29159, "znak-koncept"),
    ("Znak Horyzont",            10762, "znak-horyzont"),
    ("Znak Literanova",          10763, "znak-literanova"),
    ("Marginesy",                 5484, "marginesy"),
    ("Czwarta Strona",           11085, "czwarta-strona"),
    ("Wydawnictwo Poznańskie",   10345, "wydawnictwo-poznanskie"),
    ("Jaguar",                    4373, "jaguar"),
    ("Wydawnictwo Kobiece",      14841, "wydawnictwo-kobiece"),
    ("Otwarte",                   6803, "otwarte"),
    ("W.A.B.",                    9686, "w-a-b"),
    ("Filia",                     2918, "filia"),
    ("Sine Qua Non",              8256, "sine-qua-non"),
    ("Wydawnictwo Naukowe PWN",  10294, "wydawnictwo-naukowe-pwn"),
]

PUBLISHER_IDS: list[int] = [pid for _, pid, _ in KNOWN_PUBLISHERS]

MAX_CATALOG_PAGES = 20   # hard cap per publisher (safety)
MAX_PROBE_DEPTH   = 5    # exponential search cap: 2^5 = 32 pages explored max


# ── Publisher name matching ───────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Lowercase + strip diacritics for fuzzy name matching."""
    nfkd = unicodedata.normalize("NFKD", text.lower().strip())
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if not unicodedata.combining(c)))


_NORM_TARGETS: list[str] = [_norm(name) for name, _, _ in KNOWN_PUBLISHERS]


def _is_target_publisher(name: str) -> bool:
    if not name:
        return False
    n = _norm(name)
    return any(t in n or n in t for t in _NORM_TARGETS)


def _best_display_name(raw: str) -> str:
    if not raw:
        return raw
    n = _norm(raw)
    for display, _, _ in KNOWN_PUBLISHERS:
        t = _norm(display)
        if t in n or n in t:
            return display
    return raw


# ── URL builders ──────────────────────────────────────────────────────────────

def _pub_catalog_url(pub_id: int, year: int, page: int) -> str:
    """
    Catalog for ONE publisher + year, sorted newest-first.
    No AJAX params (listId/listType/paginatorType) — those may cause the server
    to return a partial AJAX fragment with different HTML structure.
    """
    return (
        f"{BASE_URL}/katalog/ksiazki"
        f"?publisherId[]={pub_id}"
        f"&publishedYear={year}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
    )


def _pub_catalog_url_noyear(pub_id: int, page: int) -> str:
    """
    Fallback: no publishedYear filter.
    Used when the year-filtered URL returns 0 stubs (filter may be broken or
    publisher has no books registered with that year in the database yet).
    """
    return (
        f"{BASE_URL}/katalog/ksiazki"
        f"?publisherId[]={pub_id}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
    )


# ── Date helpers ──────────────────────────────────────────────────────────────

def _is_year_only(d: date) -> bool:
    """
    Heuristic: date(year, 1, 1) is how _parse_published_date encodes a year-only
    entry ("rok wydania: 2026").  Genuine January 1 publications are rare enough
    that we treat month=1 day=1 as "unknown month within that year".
    """
    return d.month == 1 and d.day == 1


# ── Book detail parsing ───────────────────────────────────────────────────────

def _extract_publisher_name(soup: BeautifulSoup) -> Optional[str]:
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        if "wydawca" in label or "wydawnictwo" in label:
            dd = dt.find_next_sibling("dd")
            if dd:
                link = dd.find("a")
                text = (link or dd).get_text(strip=True)
                return text or None
    return None


def _parse_premiere_book(
    soup: BeautifulSoup,
    stub: dict,
    premiere_month: str,
    publisher_display: str,
) -> Optional[PremiereBook]:
    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    cover_el = soup.find("meta", {"property": "og:image"})
    isbn_el  = soup.find("meta", {"property": "books:isbn"})

    desc_el = (
        soup.select_one("div.collapse-content")
        or soup.select_one("div#book-description")
        or soup.select_one(".book-description")
        or soup.select_one("div[itemprop='description']")
    )
    description = ""
    if desc_el:
        description = re.sub(
            r"\s+", " ", desc_el.get_text(separator=" ", strip=True)
        ).strip()

    tag_els = soup.select("a[href*='/ksiazki/t/']")
    tags = list(dict.fromkeys(el.get_text(strip=True) for el in tag_els))[:10]

    return PremiereBook(
        book_id=book_id,
        title=stub["title"],
        author=stub["author"],
        publisher=publisher_display,
        premiere_month=premiere_month,
        url=stub["url"],
        cover_url=cover_el["content"] if cover_el else None,
        isbn=isbn_el["content"] if isbn_el else None,
        description=description,
        tags=tags,
    )


# ── Listing page fetcher with year-filter fallback ────────────────────────────

def _fetch_stubs(
    session, pub_id: int, year: int, page: int
) -> tuple[list[dict], bool]:
    """
    Fetch listing page stubs for a publisher/year/page.
    Returns (stubs, used_noyear_fallback).
    Tries year-filtered URL first; falls back to no-year URL if 0 stubs.
    """
    html = _fetch(session, _pub_catalog_url(pub_id, year, page))
    _sleep()
    if html:
        stubs = _parse_listing_page(html)
        if stubs:
            return stubs, False

    # Fallback: try without year filter
    logger.debug("  year-filtered URL returned 0 stubs on page %d, trying no-year fallback", page)
    html2 = _fetch(session, _pub_catalog_url_noyear(pub_id, page))
    _sleep()
    if html2:
        stubs2 = _parse_listing_page(html2)
        if stubs2:
            return stubs2, True

    return [], False


# ── Binary search helpers ─────────────────────────────────────────────────────

def _probe_first_book_date(
    session, pub_id: int, year: int, page: int, *, no_year: bool = False
) -> Optional[tuple[int, int]]:
    """
    Fetch listing page, then probe detail pages until we get a reliable date.
    Returns (year, month) tuple, or None if page is empty / all fetches failed.

    Year-only dates (month=1, day=1) are retried on the next book on the page.
    If unavoidable (all books on page are year-only), returns (year, 12) as a
    conservative upper bound — "unknown month, treat as future".

    Cost: 1 listing fetch + up to 3 detail page fetches + sleeps.
    """
    url = (_pub_catalog_url_noyear(pub_id, page) if no_year
           else _pub_catalog_url(pub_id, year, page))
    html = _fetch(session, url)
    _sleep()
    if not html:
        return None

    stubs = _parse_listing_page(html)
    if not stubs:
        if not no_year:
            # Retry with no-year fallback
            return _probe_first_book_date(session, pub_id, year, page, no_year=True)
        return None

    last_year_only: Optional[int] = None

    for stub in stubs[:3]:   # try up to 3 books to avoid year-only traps
        book_html = _fetch(session, stub["url"])
        _sleep()
        if not book_html:
            continue

        soup     = BeautifulSoup(book_html, "lxml")
        pub_date = _parse_published_date(soup)
        if pub_date is None:
            continue

        if not _is_year_only(pub_date):
            return (pub_date.year, pub_date.month)

        # Year-only — remember the year, try next book
        last_year_only = pub_date.year

    if last_year_only is not None:
        # All sampled books have year-only dates.
        # Conservative: treat as end-of-year to avoid false "past" detection.
        logger.debug("  page %d: all year-only dates, year=%d → treating as (%d,12)",
                     page, last_year_only, last_year_only)
        return (last_year_only, 12)

    return None


def _find_scan_start_page(
    session, pub_id: int, year: int, target_ym: tuple[int, int]
) -> Optional[int]:
    """
    Binary search for the first page where target-month books appear.
    Returns page number to start linear scan from, or None if none exist.
    """
    # ── Step 1: probe page 1 ───────────────────────────────────────────────────
    d1 = _probe_first_book_date(session, pub_id, year, 1)
    logger.debug("  probe(1) → %s", d1)

    if d1 is None:
        logger.info("  probe(1) empty — no books found")
        return None

    if d1 < target_ym:
        # Could be a genuine past date — skip publisher
        logger.debug("  probe(1) %s < target — skip", d1)
        return None

    if d1 == target_ym:
        logger.debug("  probe(1) == target — scan from page 1")
        return 1

    # d1 > target_ym: future books on page 1, search for transition

    # ── Step 2: exponential search for upper bound ─────────────────────────────
    lo = 1
    hi: Optional[int] = None

    probe_page = 2
    for _ in range(MAX_PROBE_DEPTH):
        probe_page = min(probe_page, MAX_CATALOG_PAGES)

        d = _probe_first_book_date(session, pub_id, year, probe_page)
        logger.debug("  probe(%d) → %s", probe_page, d)

        if d is None:
            # Past end of list — no target-month books
            return None
        if d <= target_ym:
            hi = probe_page
            break
        lo = probe_page
        next_probe = probe_page * 2
        if next_probe > MAX_CATALOG_PAGES:
            if probe_page == MAX_CATALOG_PAGES:
                return None   # still future at cap — give up
            probe_page = MAX_CATALOG_PAGES
        else:
            probe_page = next_probe

    if hi is None:
        return None

    # ── Step 3: bisect between lo (future) and hi (target-or-past) ────────────
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        d = _probe_first_book_date(session, pub_id, year, mid)
        logger.debug("  bisect(%d) → %s", mid, d)
        if d is None or d < target_ym:
            hi = mid
        elif d == target_ym:
            hi = mid
        else:
            lo = mid

    start = max(1, hi - 1)
    logger.debug("  transition at page %d → scan from page %d", hi, start)
    return start


# ── Per-publisher scraper ─────────────────────────────────────────────────────

def _scrape_one_publisher(
    session,
    pub_id: int,
    display_name: str,
    year: int,
    target_ym: tuple[int, int],
    premiere_month: str,
) -> list[PremiereBook]:
    logger.info("--- %s (id=%d) ---", display_name, pub_id)

    start_page = _find_scan_start_page(session, pub_id, year, target_ym)
    if start_page is None:
        logger.info("  no target-month books")
        return []

    logger.info("  scanning from page %d", start_page)
    books:    list[PremiereBook] = []
    seen_ids: set[str]           = set()
    no_year_fallback = False  # set True if we switched to no-year URLs

    for page in range(start_page, MAX_CATALOG_PAGES + 1):
        stubs, used_fallback = _fetch_stubs(session, pub_id, year, page)
        if used_fallback:
            no_year_fallback = True
        if not stubs:
            break

        stop_publisher = False
        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue

            book_html = _fetch(session, stub["url"])
            _sleep()
            if not book_html:
                continue

            soup     = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            if pub_date:
                if _is_year_only(pub_date):
                    # Year-only date: month is unknown.
                    # Same year → could be target month, but we can't tell → skip.
                    # Different year → use year for definite past/future decision.
                    if pub_date.year == target_ym[0]:
                        logger.debug("  year-only %s: %s — ambiguous, skip",
                                     pub_date.year, stub["title"])
                        continue
                    elif pub_date.year < target_ym[0]:
                        logger.info("  year-only past year %d: %s — stop",
                                    pub_date.year, stub["title"])
                        stop_publisher = True
                        break
                    else:
                        logger.debug("  year-only future year %d: %s — skip",
                                     pub_date.year, stub["title"])
                        continue
                else:
                    book_ym = (pub_date.year, pub_date.month)
                    if book_ym > target_ym:
                        logger.debug("  future %s: %s — skip", pub_date, stub["title"])
                        continue
                    if book_ym < target_ym:
                        logger.info("  past %s: %s — stop", pub_date, stub["title"])
                        stop_publisher = True
                        break

            # Verify publisher from detail page (safety net for imperfect URL filter).
            raw_pub = _extract_publisher_name(soup)
            if raw_pub and not _is_target_publisher(raw_pub):
                logger.debug("  publisher mismatch '%s': %s — skip",
                             raw_pub, stub["title"])
                continue
            canon_name = _best_display_name(raw_pub) if raw_pub else display_name

            premiere = _parse_premiere_book(soup, stub, premiere_month, canon_name)
            if premiere:
                books.append(premiere)
                seen_ids.add(book_id)
                logger.info("  FOUND: %-45s | %s", premiere.title, canon_name)

        if stop_publisher:
            break

    logger.info("  %s — %d books", display_name, len(books))
    return books


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_premieres_for_month(year: int, month: int) -> list[PremiereBook]:
    """
    Scrape lubimyczytac.pl for premieres from KNOWN_PUBLISHERS in the given month.

    Each publisher is processed separately:
      - Binary search to locate the target-month boundary
      - Linear scan to collect all target-month books
      - Fallback to no-year URL if year-filtered catalog returns 0 results
    """
    session        = _make_session()
    premiere_month = f"{year:04d}-{month:02d}"
    target_ym      = (year, month)

    all_books: list[PremiereBook] = []
    seen_ids:  set[str]           = set()

    logger.info(
        "=== Premieres: %s — %d publishers ===",
        premiere_month, len(KNOWN_PUBLISHERS),
    )

    for display_name, pub_id, _ in KNOWN_PUBLISHERS:
        pub_books = _scrape_one_publisher(
            session, pub_id, display_name, year, target_ym, premiere_month
        )
        for book in pub_books:
            if book.book_id not in seen_ids:
                all_books.append(book)
                seen_ids.add(book.book_id)

    logger.info(
        "=== Premieres %s complete: %d books ===",
        premiere_month, len(all_books),
    )
    return all_books
