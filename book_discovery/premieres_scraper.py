"""
Scraper for monthly book premieres from selected publishers.

Strategy:
  1. Fetch lubimyczytac.pl catalog pages filtered by publication month/year,
     sorted by publishDate descending (newest first).
  2. For each book stub, fetch the individual book page.
  3. Extract the publisher field and compare against TARGET_PUBLISHER_NAMES.
  4. Stop pagination once books older than the target month appear.

No rating filter — this newsletter is about what's coming out, not quality scores.
"""

import logging
import re
import time
import random
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


# ── Publisher matching ─────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = text.lower().strip()
    # Remove diacritics for fuzzy match (ą→a, ę→e, etc.)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_text)


_NORMALIZED_TARGETS = [_normalize(p) for p in TARGET_PUBLISHER_NAMES]


def _publisher_matches(extracted: str) -> bool:
    """Return True if extracted publisher matches any target (partial, normalised)."""
    norm = _normalize(extracted)
    for target in _NORMALIZED_TARGETS:
        if target in norm or norm in target:
            return True
    return False


# ── URL builders ───────────────────────────────────────────────────────────────

def _catalog_url(year: int, month: int, page: int) -> str:
    base = f"{BASE_URL}/katalog/ksiazki"
    qs = (
        f"listId=catalogFilteredList"
        f"&listType=list"
        f"&publishedYear={year}"
        f"&publishedMonth={month}"
        f"&orderBy=publishDate"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )
    return f"{base}?{qs}"


def _catalog_url_simple(year: int, month: int, page: int) -> str:
    """Fallback without AJAX params."""
    base = f"{BASE_URL}/katalog/ksiazki"
    return (
        f"{base}?publishedYear={year}&publishedMonth={month}"
        f"&orderBy=publishDate&desc=1&lang[]=pol&page={page}"
    )


# ── Book page parser ───────────────────────────────────────────────────────────

def _extract_publisher(soup: BeautifulSoup) -> Optional[str]:
    """Extract publisher from dt/dd pairs on the book detail page."""
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        if "wydawca" in label or "wydawnictwo" in label:
            dd = dt.find_next_sibling("dd")
            if dd:
                pub_link = dd.find("a")
                text = (pub_link or dd).get_text(strip=True)
                return text if text else None
    return None


def _parse_premiere_book(
    soup: BeautifulSoup,
    stub: dict,
    premiere_month: str,
) -> Optional[PremiereBook]:
    """Build a PremiereBook if publisher matches; return None otherwise."""
    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    publisher = _extract_publisher(soup)
    if not publisher:
        logger.debug("No publisher found: %s", stub["title"])
        return None

    if not _publisher_matches(publisher):
        logger.debug("Publisher '%s' not in target list: %s", publisher, stub["title"])
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
        publisher=publisher,
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
    Scrape lubimyczytac.pl catalog for books published in the given month
    from target publishers.

    Args:
        year:  e.g. 2026
        month: 1–12

    Returns:
        List of PremiereBook objects (may be empty if no matches found).
    """
    session = _make_session()
    premiere_month = f"{year:04d}-{month:02d}"
    target_ym = (year, month)

    books: list[PremiereBook] = []
    seen_ids: set[str] = set()

    logger.info("=== Premieres scrape: %s ===", premiere_month)

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = _catalog_url(year, month, page)
        logger.info("[premieres/%s] catalog page %d", premiere_month, page)
        logger.debug("[premieres/%s] → %s", premiere_month, url)

        html = _fetch(session, url)
        if not html:
            break
        _sleep()

        stubs = _parse_listing_page(html)

        if not stubs and page == 1:
            fb = _catalog_url_simple(year, month, page)
            logger.info("[premieres/%s] fallback URL → %s", premiere_month, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            logger.info("[premieres/%s] No stubs on page %d — done.", premiere_month, page)
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
                    logger.info(
                        "[premieres/%s] year %d < target — past month, stopping.",
                        premiere_month, pub_year,
                    )
                    reached_past = True
                    break
                if pub_year > year:
                    continue  # future book, skip

            # Fetch individual book page
            book_html = _fetch(session, stub["url"])
            if not book_html:
                _sleep()
                continue
            _sleep()

            soup = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            if pub_date:
                book_ym = (pub_date.year, pub_date.month)
                if book_ym < target_ym:
                    logger.info(
                        "[premieres/%s] pub_date %s before target — stopping.",
                        premiere_month, pub_date,
                    )
                    reached_past = True
                    break
                if book_ym > target_ym:
                    continue  # future month

            premiere = _parse_premiere_book(soup, stub, premiere_month)
            if premiere:
                books.append(premiere)
                seen_ids.add(book_id)
                logger.info(
                    "[premieres/%s] MATCH: %s | %s",
                    premiere_month, premiere.title, premiere.publisher,
                )

        logger.info(
            "[premieres/%s] page %d done — %d matches so far",
            premiere_month, page, len(books),
        )

        if reached_past:
            break

    logger.info("Premieres %s complete: %d books from target publishers.", premiere_month, len(books))
    return books
