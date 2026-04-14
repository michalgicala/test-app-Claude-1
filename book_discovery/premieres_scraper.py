"""
Premieres scraper v5 — per-publisher binary search + linear scan.

History:
  v1/v2: Catalog publishedMonth=4 → showed wrong month (0-indexed)
  v3:    Publisher pages /wydawnictwo/{id}/{slug}/ksiazki → ignored all params
  v4:    All-publisher catalog → wrong IDs (0 results); correct IDs still
         returned 0 due to unknown catalog filtering behaviour
  v5:    One publisher at a time + binary search for target-month boundary.

Algorithm per publisher:
  1. Probe page 1: fetch listing + first book detail page → get exact date.
  2. date < target → skip publisher (newest book is already older than target).
     date == target → scan from page 1.
     date > target → binary search (exponential then bisect) for the page where
                     the first book transitions to target-or-past month.
  3. Start linear scan from max(1, transition_page - 1) to avoid missing
     target-month books near the bottom of the page before the transition.
  4. In linear scan: skip future books, collect target-month books, stop at past.
"""

import logging
import re
import unicodedata
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


# ── URL builder ───────────────────────────────────────────────────────────────

def _pub_catalog_url(pub_id: int, year: int, page: int) -> str:
    """Catalog filtered by ONE publisher + year, sorted newest-first."""
    return (
        f"{BASE_URL}/katalog/ksiazki"
        f"?listId=catalogFilteredList"
        f"&listType=list"
        f"&publisherId[]={pub_id}"
        f"&publishedYear={year}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )


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


# ── Binary search helpers ─────────────────────────────────────────────────────

def _probe_first_book_date(
    session, pub_id: int, year: int, page: int
) -> Optional[tuple[int, int]]:
    """
    Fetch listing page, then first book's detail page.
    Returns (year, month) of first book, or None if empty / fetch failed.
    Cost: 2 HTTP requests + 2 sleeps.
    """
    html = _fetch(session, _pub_catalog_url(pub_id, year, page))
    _sleep()
    if not html:
        return None

    stubs = _parse_listing_page(html)
    if not stubs:
        return None

    book_html = _fetch(session, stubs[0]["url"])
    _sleep()
    if not book_html:
        return None

    soup     = BeautifulSoup(book_html, "lxml")
    pub_date = _parse_published_date(soup)
    if not pub_date:
        return None

    return (pub_date.year, pub_date.month)


def _find_scan_start_page(
    session, pub_id: int, year: int, target_ym: tuple[int, int]
) -> Optional[int]:
    """
    Find the page to start linear scanning from.

    Returns None  → no target-month books for this publisher/year.
    Returns page# → start scanning here (may be 1 before the transition to
                    ensure no target-month books at the bottom of a page are missed).
    """
    # ── Step 1: probe page 1 ───────────────────────────────────────────────────
    d1 = _probe_first_book_date(session, pub_id, year, 1)
    if d1 is None:
        logger.debug("  probe(1) → empty")
        return None
    if d1 < target_ym:
        logger.debug("  probe(1) → %s — already past, skip", d1)
        return None
    if d1 == target_ym:
        logger.debug("  probe(1) → target month, scan from page 1")
        return 1

    # ── Step 2: exponential search for upper bound ─────────────────────────────
    # page 1 = known future (d1 > target_ym)
    lo = 1
    hi: Optional[int] = None

    probe_page = 2
    for _ in range(MAX_PROBE_DEPTH):
        probe_page = min(probe_page, MAX_CATALOG_PAGES)

        d = _probe_first_book_date(session, pub_id, year, probe_page)
        logger.debug("  probe(%d) → %s", probe_page, d)

        if d is None:
            # Past end of list — no target-month books this year
            return None
        if d <= target_ym:
            hi = probe_page
            break
        lo = probe_page
        next_probe = probe_page * 2
        if next_probe > MAX_CATALOG_PAGES:
            if probe_page == MAX_CATALOG_PAGES:
                # Reached cap and still future
                return None
            probe_page = MAX_CATALOG_PAGES
        else:
            probe_page = next_probe

    if hi is None:
        return None   # never found a target-or-past page

    # ── Step 3: binary search between lo (future) and hi (target-or-past) ──────
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        d = _probe_first_book_date(session, pub_id, year, mid)
        logger.debug("  bisect probe(%d) → %s", mid, d)
        if d is None or d < target_ym:
            hi = mid   # overshot or empty, move boundary earlier
        elif d == target_ym:
            hi = mid   # found target, try even earlier
        else:
            lo = mid   # still future

    # hi = first page where first book is <= target_ym.
    # Scan from hi-1 to catch target-month books at the bottom of the previous page.
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

    for page in range(start_page, MAX_CATALOG_PAGES + 1):
        html = _fetch(session, _pub_catalog_url(pub_id, year, page))
        _sleep()
        if not html:
            break

        stubs = _parse_listing_page(html)
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
                book_ym = (pub_date.year, pub_date.month)
                if book_ym > target_ym:
                    logger.debug("  future %s: %s — skip", pub_date, stub["title"])
                    continue
                if book_ym < target_ym:
                    logger.info("  past %s: %s — stop", pub_date, stub["title"])
                    stop_publisher = True
                    break

            # Verify publisher from detail page — safety net if publisherId[] filter
            # returned books from other publishers (URL filter is best-effort).
            raw_pub = _extract_publisher_name(soup)
            if raw_pub and not _is_target_publisher(raw_pub):
                logger.debug("  publisher mismatch '%s': %s — skip", raw_pub, stub["title"])
                continue
            # Use canonical display name from registry, falling back to URL-based name.
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
      - Binary search to locate the target-month boundary (avoids scanning entire year)
      - Linear scan from the boundary to collect all target-month books
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
