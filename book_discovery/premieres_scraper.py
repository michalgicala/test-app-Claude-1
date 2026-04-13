"""
Premieres scraper v3 — per-publisher pages.

Why publisher-specific pages instead of a filtered catalog:
  The catalog's publisherId[] / publishedMonth URL parameters are unreliable
  (the catalog returns books from all dates, causing every result to be skipped
  as pub_date > target_month).  Publisher pages (/wydawnictwo/{id}/{slug}/ksiazki)
  embed the publisher in the URL path, so the filter is guaranteed.

Strategy per publisher:
  1. Hit /wydawnictwo/{id}/{slug}/ksiazki?publishedYear={year}&orderBy=publishDate&desc=1
  2. Parse listing page stubs (title, author, URL, pub_year from card).
  3. Skip stubs from the wrong year.
  4. Fetch detail page for exact pub_month + description / cover / ISBN.
  5. Stop when pub_date falls before the target month.

Typical HTTP requests per month:
  ~1-2 listing pages × 10 publishers = ~15 pages
  ~5-10 book detail pages × 10 publishers = ~75 detail pages
  Total: ~90 requests  (vs. 800+ with the broken catalog approach)
"""

import logging
import re
import unicodedata
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from .models import PremiereBook
from .config import BASE_URL, MAX_CATALOG_PAGES
from .scraper import (
    _make_session,
    _fetch,
    _sleep,
    _parse_listing_page,
    _parse_published_date,
    extract_book_id,
)

logger = logging.getLogger(__name__)

MAX_PAGES_PER_PUBLISHER = 10  # safety cap per publisher

# ── Publisher registry ────────────────────────────────────────────────────────
# (display_name, lubimyczytac_publisher_id, url_slug)
# IDs can be verified by visiting lubimyczytac.pl/wydawnictwo/{id}/{slug}.
# If an ID is wrong the scraper simply logs 0 books for that publisher and
# continues — no crash.

KNOWN_PUBLISHERS: list[tuple[str, int, str]] = [
    ("Marginesy",               2951, "marginesy"),
    ("Znak",                      49, "znak"),
    ("Czwarta Strona",          3427, "czwarta-strona"),
    ("Wydawnictwo Poznańskie",   160, "wydawnictwo-poznanskie"),
    ("Jaguar",                  3001, "jaguar"),
    ("Kobiece",                 2852, "kobiece"),
    ("Otwarte",                 1965, "otwarte"),
    ("W.A.B.",                    60, "wab"),
    ("Filia",                   2876, "filia"),
    ("SQN",                     2836, "sqn"),
]


# ── URL builders ──────────────────────────────────────────────────────────────

def _pub_url(pub_id: int, slug: str, year: int, page: int) -> str:
    """Publisher books page filtered to one year, sorted newest first."""
    base = f"{BASE_URL}/wydawnictwo/{pub_id}/{slug}/ksiazki"
    return (
        f"{base}?publishedYear={year}"
        f"&orderBy=publishDate&desc=1&lang[]=pol"
        f"&page={page}&paginatorType=url"
    )


def _pub_url_simple(pub_id: int, slug: str, year: int, page: int) -> str:
    """Fallback without AJAX params."""
    base = f"{BASE_URL}/wydawnictwo/{pub_id}/{slug}/ksiazki"
    return f"{base}?publishedYear={year}&orderBy=publishDate&desc=1&lang[]=pol&page={page}"


def _pub_url_no_year(pub_id: int, slug: str, page: int) -> str:
    """Second fallback — no year filter, in case publishedYear param isn't supported."""
    base = f"{BASE_URL}/wydawnictwo/{pub_id}/{slug}/ksiazki"
    return f"{base}?orderBy=publishDate&desc=1&lang[]=pol&page={page}"


# ── Book detail parsing ───────────────────────────────────────────────────────

def _extract_publisher_name(soup: BeautifulSoup) -> Optional[str]:
    """Read the canonical publisher name from a book's dt/dd section."""
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
    """Build a PremiereBook from a parsed book detail page."""
    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    # Use canonical name from page if available, fallback to registry display name
    page_publisher = _extract_publisher_name(soup)
    publisher = page_publisher or publisher_display

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
        publisher=publisher,
        premiere_month=premiere_month,
        url=stub["url"],
        cover_url=cover_el["content"] if cover_el else None,
        isbn=isbn_el["content"] if isbn_el else None,
        description=description,
        tags=tags,
    )


# ── Per-publisher scrape ──────────────────────────────────────────────────────

def _scrape_one_publisher(
    session,
    pub_display: str,
    pub_id: int,
    pub_slug: str,
    year: int,
    month: int,
    premiere_month: str,
    seen_ids: set[str],
) -> list[PremiereBook]:
    """
    Scrape one publisher's release page for books published in target month.

    Sorted newest-first, so we encounter target-month books quickly and stop
    as soon as we hit the previous month.
    """
    books: list[PremiereBook] = []
    target_ym = (year, month)
    label = f"[premieres/{premiere_month}/{pub_display}]"

    used_no_year_fallback = False

    for page in range(1, MAX_PAGES_PER_PUBLISHER + 1):
        url = _pub_url(pub_id, pub_slug, year, page)
        logger.info("%s page %d", label, page)
        logger.debug("%s → %s", label, url)

        html = _fetch(session, url)
        if not html:
            break
        _sleep()

        stubs = _parse_listing_page(html)

        # Fallback 1: simpler URL (no AJAX params)
        if not stubs and page == 1:
            fb = _pub_url_simple(pub_id, pub_slug, year, page)
            logger.info("%s fallback (simple): %s", label, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        # Fallback 2: drop year filter (publisher page may not support it)
        if not stubs and page == 1 and not used_no_year_fallback:
            fb2 = _pub_url_no_year(pub_id, pub_slug, page)
            logger.info("%s fallback (no year filter): %s", label, fb2)
            html3 = _fetch(session, fb2)
            if html3:
                stubs = _parse_listing_page(html3)
                _sleep()
            used_no_year_fallback = True

        if not stubs:
            logger.info("%s empty page %d — done.", label, page)
            break

        reached_past = False

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue

            # Cheap year pre-filter from listing card
            pub_year = stub.get("pub_year")
            if pub_year is not None:
                if pub_year < year:
                    logger.info("%s year %d < %d on listing — stopping.", label, pub_year, year)
                    reached_past = True
                    break
                if pub_year > year:
                    continue  # pre-announced future book — skip

            # Fetch detail page for exact date + description / cover / ISBN
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
                        "%s pub_date %s < %s — past month, stopping.",
                        label, pub_date, premiere_month,
                    )
                    reached_past = True
                    break
                if book_ym > target_ym:
                    logger.debug(
                        "%s pub_date %s > %s — future book, skipping.",
                        label, pub_date, premiere_month,
                    )
                    continue

            premiere = _parse_premiere_book(soup, stub, premiere_month, pub_display)
            if premiere:
                books.append(premiere)
                seen_ids.add(book_id)
                logger.info("%s FOUND: %s", label, premiere.title)

        logger.info("%s page %d done — %d found so far", label, page, len(books))

        if reached_past:
            break

    logger.info("%s total for this publisher: %d books", label, len(books))
    return books


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_premieres_for_month(year: int, month: int) -> list[PremiereBook]:
    """
    Scrape all target publishers' release pages for premieres in the given month.

    Each publisher is scraped independently using its dedicated page
    (/wydawnictwo/{id}/{slug}/ksiazki), so the publisher filter is always
    applied via the URL path rather than an uncertain query parameter.

    Args:
        year:  e.g. 2026
        month: 1–12

    Returns:
        Deduplicated list of PremiereBook objects.
    """
    session        = _make_session()
    premiere_month = f"{year:04d}-{month:02d}"
    seen_ids:  set[str]          = set()
    all_books: list[PremiereBook] = []

    logger.info("=== Premieres scrape: %s — %d publishers ===", premiere_month, len(KNOWN_PUBLISHERS))

    for pub_display, pub_id, pub_slug in KNOWN_PUBLISHERS:
        books = _scrape_one_publisher(
            session,
            pub_display, pub_id, pub_slug,
            year, month, premiere_month,
            seen_ids,
        )
        all_books.extend(books)

    logger.info(
        "=== Premieres %s complete: %d total books from %d publishers ===",
        premiere_month, len(all_books), len(KNOWN_PUBLISHERS),
    )
    return all_books
