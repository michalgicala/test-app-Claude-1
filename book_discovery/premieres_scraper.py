"""
Premieres scraper v4 — catalog with year+publisher filter, month in code.

History of what didn't work:
  v1/v2: Catalog with publishedMonth=4 → showed May 2026 books (0-indexed month?)
  v3:    Publisher pages /wydawnictwo/{id}/{slug}/ksiazki
           → orderBy/publishedYear params ignored; books shown alphabetically,
             first book from 2009 triggered immediate "past month" stop.
  v4a:   Wrong publisher IDs in KNOWN_PUBLISHERS → publisherId[] filter returned 0.

v4 approach:
  • Use /katalog/ksiazki — same AJAX infrastructure as category pages, date
    sort confirmed working.
  • publishedYear={year}    — year-level filter (reliable).
  • NO publishedMonth param — unreliable (0-indexed? ignored?).
  • publisherId[]={id}      — IDs verified from lubimyczytac.pl publisher URLs.
  • orderBy=publishDate&desc=1 — newest first.
  • Month checked in code: skip future months, stop at past months.
  • "max consecutive all-future pages" guard (MAX_FUTURE_PAGES) prevents
    infinite scanning when IDs don't filter and many future books appear.

Typical requests per month (publisher IDs working):
  ~2-5 catalog pages + ~60-80 detail pages ≈ 85 requests
Fallback (IDs not working, all 2026 books scanned):
  ~6-8 catalog pages + detail pages for only April books ≈ 100 requests
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
# To verify an ID: open https://lubimyczytac.pl/wydawnictwo/{id}/{slug}

KNOWN_PUBLISHERS: list[tuple[str, int, str]] = [
    # IDs extracted directly from lubimyczytac.pl publisher URLs (verified)
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

MAX_CATALOG_PAGES = 15  # safety cap — with year+publisher filter ≪ 15 pages
MAX_FUTURE_PAGES  = 5   # stop if N consecutive pages are all future-month books


# ── Publisher name matching ───────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Lowercase + strip diacritics."""
    nfkd = unicodedata.normalize("NFKD", text.lower().strip())
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if not unicodedata.combining(c)))


_NORM_TARGETS: list[str] = [_norm(name) for name, _, _ in KNOWN_PUBLISHERS]


def _is_target_publisher(name: str) -> bool:
    """Return True if the publisher name matches any entry in KNOWN_PUBLISHERS."""
    if not name:
        return False
    n = _norm(name)
    for target in _NORM_TARGETS:
        if target in n or n in target:
            return True
    return False


def _best_display_name(raw: str) -> str:
    """Return the canonical display name for a matched publisher."""
    if not raw:
        return raw
    n = _norm(raw)
    for display, _, _ in KNOWN_PUBLISHERS:
        target = _norm(display)
        if target in n or n in target:
            return display
    return raw


# ── URL builders ──────────────────────────────────────────────────────────────

def _catalog_url(year: int, page: int) -> str:
    """Catalog filtered by our publisher IDs + year, sorted by date desc."""
    pub_qs = "".join(f"&publisherId[]={pid}" for pid in PUBLISHER_IDS)
    return (
        f"{BASE_URL}/katalog/ksiazki"
        f"?listId=catalogFilteredList"
        f"&listType=list"
        f"{pub_qs}"
        f"&publishedYear={year}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )


def _catalog_url_simple(year: int, page: int) -> str:
    """Fallback without AJAX params."""
    pub_qs = "".join(f"&publisherId[]={pid}" for pid in PUBLISHER_IDS)
    return (
        f"{BASE_URL}/katalog/ksiazki"
        f"?publishedYear={year}"
        f"{pub_qs}"
        f"&orderBy=publishDate&desc=1&lang[]=pol&page={page}"
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


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_premieres_for_month(year: int, month: int) -> list[PremiereBook]:
    """
    Scrape lubimyczytac.pl for premieres from target publishers in the given month.

    Uses the catalog filtered by publisher IDs + year, sorted newest-first.
    Month is checked on each book's detail page. Publisher is verified against
    KNOWN_PUBLISHERS regardless of whether the URL publisher filter worked.
    """
    session        = _make_session()
    premiere_month = f"{year:04d}-{month:02d}"
    target_ym      = (year, month)

    books:    list[PremiereBook] = []
    seen_ids: set[str]           = set()

    future_only_pages = 0   # consecutive pages where every book was from a future month

    logger.info(
        "=== Premieres: %s — catalog with %d publisher IDs, year=%d ===",
        premiere_month, len(PUBLISHER_IDS), year,
    )

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = _catalog_url(year, page)
        logger.info("[premieres/%s] page %d", premiere_month, page)
        logger.debug("[premieres/%s] → %s", premiere_month, url)

        html = _fetch(session, url)
        if not html:
            logger.warning("[premieres/%s] fetch failed for page %d", premiere_month, page)
            break
        _sleep()

        stubs = _parse_listing_page(html)

        if not stubs and page == 1:
            fb = _catalog_url_simple(year, page)
            logger.info("[premieres/%s] fallback URL: %s", premiere_month, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            logger.info("[premieres/%s] empty page %d — done.", premiere_month, page)
            break

        page_has_target = False
        reached_past    = False

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue

            # Quick year pre-check from listing card (avoids a detail-page fetch)
            pub_year = stub.get("pub_year")
            if pub_year is not None:
                if pub_year < year:
                    logger.info(
                        "[premieres/%s] listing year %d < %d — past, stopping.",
                        premiere_month, pub_year, year,
                    )
                    reached_past = True
                    break
                if pub_year > year:
                    continue  # pre-announced future year

            # Fetch detail page for exact date + publisher + description
            book_html = _fetch(session, stub["url"])
            if not book_html:
                _sleep()
                continue
            _sleep()

            soup     = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            if pub_date:
                book_ym = (pub_date.year, pub_date.month)
                if book_ym > target_ym:
                    logger.debug(
                        "[premieres/%s] future date %s: %s — skip",
                        premiere_month, pub_date, stub["title"],
                    )
                    continue  # future book — keep scanning
                if book_ym < target_ym:
                    logger.info(
                        "[premieres/%s] past date %s: %s — stop",
                        premiere_month, pub_date, stub["title"],
                    )
                    reached_past = True
                    break
                # else book_ym == target_ym — fall through to publisher check

            # Publisher check (works even if publisherId[] param was ignored)
            raw_publisher = _extract_publisher_name(soup)
            if raw_publisher and not _is_target_publisher(raw_publisher):
                logger.debug(
                    "[premieres/%s] publisher '%s' not in target list: %s",
                    premiere_month, raw_publisher, stub["title"],
                )
                continue

            display_name = _best_display_name(raw_publisher or "")
            premiere = _parse_premiere_book(soup, stub, premiere_month, display_name or stub.get("title", "?"))
            if premiere:
                books.append(premiere)
                seen_ids.add(book_id)
                page_has_target = True
                logger.info(
                    "[premieres/%s] FOUND: %-40s | %s",
                    premiere_month, premiere.title, premiere.publisher,
                )

        logger.info(
            "[premieres/%s] page %d done — %d books total",
            premiere_month, page, len(books),
        )

        if reached_past:
            logger.info("[premieres/%s] Past target month — stopping.", premiere_month)
            break

        if not page_has_target:
            future_only_pages += 1
            logger.info(
                "[premieres/%s] No target-month books on page %d (%d/%d future-only pages)",
                premiere_month, page, future_only_pages, MAX_FUTURE_PAGES,
            )
            if future_only_pages >= MAX_FUTURE_PAGES:
                logger.warning(
                    "[premieres/%s] %d consecutive pages with no %s books — aborting.",
                    premiere_month, MAX_FUTURE_PAGES, premiere_month,
                )
                break
        else:
            future_only_pages = 0

    logger.info(
        "=== Premieres %s complete: %d books ===",
        premiere_month, len(books),
    )
    return books
