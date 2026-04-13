"""
Premieres scraper v2 — publisher-filtered catalog.

Performance improvement over v1:
  Instead of fetching every book in a month and then checking each
  individual page for the publisher, this version:

    1. Resolves publisher IDs once at the start of a run (hardcoded table
       with an HTTP-based discovery fallback for unknown names).
    2. Builds a catalog URL with publisherId[] filters so lubimyczytac.pl
       itself narrows the result set to only our 10 publishers.
    3. Paginates through those pre-filtered pages (typically 3–6 pages for
       ~50–80 books per month, vs. 30–40 catalog pages + ~800 detail pages
       before).

HTTP requests per month:
  v1: ~40 catalog pages × discovery = hundreds of detail page requests
  v2: 10 ID-discovery requests (once) + ~5 catalog pages + ~60 detail pages
"""

import logging
import re
import unicodedata
import urllib.parse
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from .models import PremiereBook
from .config import (
    BASE_URL,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    TARGET_PUBLISHER_NAMES,
    MAX_CATALOG_PAGES,
)
from .scraper import (
    _make_session,
    _fetch,
    _sleep,
    _parse_listing_page,
    _parse_published_date,
    extract_book_id,
)

logger = logging.getLogger(__name__)


# ── Publisher ID registry ──────────────────────────────────────────────────────
# Hardcoded fallback IDs so the scraper works immediately without HTTP discovery.
# If lubimyczytac.pl reassigns an ID the discovery function will find the new one.
# Key: lowercase display name from TARGET_PUBLISHER_NAMES (exact match).

KNOWN_PUBLISHER_IDS: dict[str, tuple[int, str]] = {
    "marginesy":                   (2951, "marginesy"),
    "znak":                        (49,   "znak"),
    "czwarta strona":              (3427, "czwarta-strona"),
    "wydawnictwo poznańskie":      (160,  "wydawnictwo-poznanskie"),
    "jaguar":                      (3001, "jaguar"),
    "kobiece":                     (2852, "kobiece"),
    "otwarte":                     (1965, "otwarte"),
    "w.a.b":                       (60,   "wab"),
    "wab":                         (60,   "wab"),
    "filia":                       (2876, "filia"),
    "sqn":                         (2836, "sqn"),
}


# ── Publisher ID discovery ─────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase + strip combining diacritics (ą→a etc.) for fuzzy matching."""
    text = text.lower().strip()
    nfkd = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if not unicodedata.combining(c)))


def _discover_publisher_id(
    session: cffi_requests.Session,
    name: str,
) -> Optional[tuple[int, str]]:
    """
    Search lubimyczytac.pl for a publisher by name.

    Hits /szukaj/szukaj?what=wydawnictwa&szukaj=<name> and looks for the
    first /wydawnictwo/{id}/{slug} link in the results.

    Returns (publisher_id, publisher_slug) or None on failure.
    """
    url = (
        f"{BASE_URL}/szukaj/szukaj"
        f"?szukaj={urllib.parse.quote(name)}&what=wydawnictwa"
    )
    html = _fetch(session, url)
    if not html:
        return None
    _sleep()

    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href*='/wydawnictwo/']"):
        href = a.get("href", "")
        m = re.search(r"/wydawnictwo/(\d+)/([^/?#\s]+)", href)
        if m:
            pub_id   = int(m.group(1))
            pub_slug = m.group(2)
            logger.debug("Discovered publisher '%s' → id=%d slug=%s", name, pub_id, pub_slug)
            return pub_id, pub_slug
    return None


def resolve_publisher_ids(session: cffi_requests.Session) -> list[int]:
    """
    Return a deduplicated list of numeric publisher IDs for all target publishers.

    Resolution order per publisher name:
      1. Hardcoded table (instant, no HTTP)
      2. lubimyczytac.pl search (one HTTP request, logs the discovered ID)
      3. Warn and skip if neither succeeds.
    """
    seen:   set[int]  = set()
    result: list[int] = []

    for name in TARGET_PUBLISHER_NAMES:
        key = name.lower()

        # 1. Hardcoded table
        entry = KNOWN_PUBLISHER_IDS.get(key)
        if entry:
            pub_id, pub_slug = entry
            source = "table"
        else:
            # 2. Dynamic discovery via search
            discovered = _discover_publisher_id(session, name)
            if discovered:
                pub_id, pub_slug = discovered
                source = "search"
            else:
                logger.warning("Publisher '%s': ID not found — skipping.", name)
                continue

        if pub_id not in seen:
            seen.add(pub_id)
            result.append(pub_id)
            logger.info("Publisher %-30s → id=%-6d slug=%-30s (%s)", name, pub_id, pub_slug, source)

    logger.info("Total unique publisher IDs: %d", len(result))
    return result


# ── URL builders ───────────────────────────────────────────────────────────────

def _catalog_url(publisher_ids: list[int], year: int, month: int, page: int) -> str:
    """Catalog URL pre-filtered by publisher IDs + publication month."""
    base     = f"{BASE_URL}/katalog/ksiazki"
    pub_qs   = "".join(f"&publisherId[]={pid}" for pid in publisher_ids)
    qs = (
        f"listId=catalogFilteredList"
        f"&listType=list"
        f"{pub_qs}"
        f"&publishedYear={year}"
        f"&publishedMonth={month}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )
    return f"{base}?{qs}"


def _catalog_url_simple(publisher_ids: list[int], year: int, month: int, page: int) -> str:
    """Fallback catalog URL without AJAX params."""
    base   = f"{BASE_URL}/katalog/ksiazki"
    pub_qs = "".join(f"&publisherId[]={pid}" for pid in publisher_ids)
    return (
        f"{base}?publishedYear={year}&publishedMonth={month}"
        f"{pub_qs}&orderBy=publishDate&desc=1&lang[]=pol&page={page}"
    )


# ── Book detail page parsing ───────────────────────────────────────────────────

def _extract_publisher_name(soup: BeautifulSoup) -> Optional[str]:
    """Read the publisher name from the book's detail page (dt/dd pairs)."""
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
    publisher_name: Optional[str],
) -> Optional[PremiereBook]:
    """Build a PremiereBook from a parsed book detail page."""
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
        publisher=publisher_name or "?",
        premiere_month=premiere_month,
        url=stub["url"],
        cover_url=cover_el["content"] if cover_el else None,
        isbn=isbn_el["content"] if isbn_el else None,
        description=description,
        tags=tags,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def scrape_premieres_for_month(year: int, month: int) -> list[PremiereBook]:
    """
    Scrape lubimyczytac.pl for premieres from target publishers in the given month.

    The catalog is pre-filtered by publisherId[] so only books from our publishers
    are returned — far fewer pages and detail requests than a full-catalog scan.

    Args:
        year:  e.g. 2026
        month: 1–12

    Returns:
        List of PremiereBook objects.
    """
    session       = _make_session()
    premiere_month = f"{year:04d}-{month:02d}"
    target_ym     = (year, month)

    # ── Step 1: resolve publisher IDs ────────────────────────────────────────
    logger.info("=== Premieres: %s — resolving publisher IDs ===", premiere_month)
    publisher_ids = resolve_publisher_ids(session)

    if not publisher_ids:
        logger.error("[premieres/%s] No publisher IDs resolved — aborting.", premiere_month)
        return []

    # ── Step 2: paginate through filtered catalog ────────────────────────────
    books:    list[PremiereBook] = []
    seen_ids: set[str]           = set()

    logger.info(
        "[premieres/%s] Fetching catalog filtered by %d publisher IDs",
        premiere_month, len(publisher_ids),
    )

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = _catalog_url(publisher_ids, year, month, page)
        logger.info("[premieres/%s] catalog page %d", premiere_month, page)
        logger.debug("[premieres/%s] → %s", premiere_month, url)

        html = _fetch(session, url)
        if not html:
            break
        _sleep()

        stubs = _parse_listing_page(html)

        if not stubs and page == 1:
            fb = _catalog_url_simple(publisher_ids, year, month, page)
            logger.info("[premieres/%s] fallback URL: %s", premiere_month, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            logger.info("[premieres/%s] Empty page %d — done.", premiere_month, page)
            break

        reached_past = False

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue

            # Cheap year pre-check from listing card
            pub_year = stub.get("pub_year")
            if pub_year is not None:
                if pub_year < year:
                    logger.info(
                        "[premieres/%s] Listing year %d < %d — end of month, stopping.",
                        premiere_month, pub_year, year,
                    )
                    reached_past = True
                    break
                if pub_year > year:
                    continue

            # Fetch book detail page for description / cover / ISBN / exact date
            book_html = _fetch(session, stub["url"])
            if not book_html:
                _sleep()
                continue
            _sleep()

            soup     = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            if pub_date:
                book_ym = (pub_date.year, pub_date.month)
                if book_ym < target_ym:
                    logger.info(
                        "[premieres/%s] pub_date %s < target — end of month, stopping.",
                        premiere_month, pub_date,
                    )
                    reached_past = True
                    break
                if book_ym > target_ym:
                    continue

            publisher_name = _extract_publisher_name(soup)
            premiere = _parse_premiere_book(soup, stub, premiere_month, publisher_name)
            if premiere:
                books.append(premiere)
                seen_ids.add(book_id)
                logger.info(
                    "[premieres/%s] FOUND: %-40s | %s",
                    premiere_month, premiere.title, premiere.publisher,
                )

        logger.info(
            "[premieres/%s] page %d done — %d books so far",
            premiere_month, page, len(books),
        )

        if reached_past:
            break

    logger.info(
        "[premieres/%s] Complete: %d premieres from target publishers.",
        premiere_month, len(books),
    )
    return books
