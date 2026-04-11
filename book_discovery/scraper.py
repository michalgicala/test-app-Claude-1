"""
Scraper for lubimyczytac.pl

Uses curl_cffi to impersonate Chrome's TLS fingerprint (plain requests returns 403).
Fetches category listing pages, then individual book pages for accurate rating data.
"""

import logging
import re
import time
import random
import urllib.parse
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from .models import Book
from .config import (
    BASE_URL,
    MIN_RATING,
    MIN_RATINGS_COUNT,
    MAX_PAGES_PER_CATEGORY,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://lubimyczytac.pl/",
}


def _make_session() -> cffi_requests.Session:
    return cffi_requests.Session(impersonate="chrome124")


def _sleep():
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def _fetch(session: cffi_requests.Session, url: str) -> Optional[str]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.text
        logger.warning("HTTP %s for %s", resp.status_code, url)
        return None
    except Exception as e:
        logger.error("Request failed for %s: %s", url, e)
        return None


def extract_book_id(url: str) -> Optional[str]:
    """Extract numeric book ID from lubimyczytac.pl URL.

    /ksiazka/4879823/sapiens  →  '4879823'
    """
    m = re.search(r"/ksiazka/(\d+)/", url)
    return m.group(1) if m else None


def _build_listing_url(category_id: int, category_slug: str, page: int) -> str:
    params = {
        "listId": "booksFilteredList",
        "listType": "list",
        "orderBy": "ratings",
        "lang[]": "pol",
        "page": page,
        "paginatorType": "url",
    }
    qs = urllib.parse.urlencode(params, doseq=True)
    return f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}?{qs}"


def _parse_listing_page(html: str) -> list[dict]:
    """Parse a category listing page. Returns list of {title, author, url} dicts."""
    soup = BeautifulSoup(html, "lxml")
    books = []

    # Primary selector — book cards on category listing pages
    cards = soup.select("div.authorAllBooks__single")

    # Fallback selector — some pages use a different layout
    if not cards:
        cards = soup.select("div.listBook")

    for card in cards:
        title_el = (
            card.select_one("a.authorAllBooks__singleTextTitle")
            or card.select_one("a.title")
            or card.select_one(".book-title a")
        )
        author_el = (
            card.select_one("a[href*='/autor/']")
            or card.select_one(".authorAllBooks__singleTextAuthor a")
        )

        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        url = href if href.startswith("http") else BASE_URL + href
        author = author_el.get_text(strip=True) if author_el else "Nieznany autor"

        books.append({"title": title, "author": author, "url": url})

    logger.debug("Parsed %d book stubs from listing page", len(books))
    return books


def _parse_book_page(html: str, stub: dict, category: str, category_label: str) -> Optional[Book]:
    """Parse an individual book page and return a Book object."""
    soup = BeautifulSoup(html, "lxml")

    # --- Rating (Open Graph meta tags — most reliable) ---
    rating_el = soup.find("meta", {"property": "books:rating:value"})
    count_el = soup.find("meta", {"property": "books:rating:count"})

    if not rating_el or not count_el:
        logger.debug("No rating meta tags found for %s", stub["url"])
        return None

    try:
        rating = float(rating_el["content"].replace(",", "."))
        ratings_count = int(count_el["content"])
    except (KeyError, ValueError):
        return None

    # --- Apply filters early to avoid unnecessary processing ---
    if rating < MIN_RATING or ratings_count < MIN_RATINGS_COUNT:
        return None

    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    # --- Cover ---
    cover_el = soup.find("meta", {"property": "og:image"})
    cover_url = cover_el["content"] if cover_el else None

    # --- ISBN ---
    isbn_el = soup.find("meta", {"property": "books:isbn"})
    isbn = isbn_el["content"] if isbn_el else None

    # --- Description ---
    desc_el = soup.select_one("div.collapse-content") or soup.select_one(".book-description")
    description = ""
    if desc_el:
        description = desc_el.get_text(separator=" ", strip=True)
        description = re.sub(r"\s+", " ", description).strip()

    # --- Tags / genres ---
    tag_els = soup.select("a[href*='/ksiazki/t/']")
    tags = list(dict.fromkeys(el.get_text(strip=True) for el in tag_els))[:10]

    # --- Empik URL ---
    empik_url = (
        "https://www.empik.com/szukaj/produkt?q="
        + urllib.parse.quote(stub["title"])
    )

    return Book(
        book_id=book_id,
        title=stub["title"],
        author=stub["author"],
        category=category,
        category_label=category_label,
        rating=rating,
        ratings_count=ratings_count,
        url=stub["url"],
        isbn=isbn,
        cover_url=cover_url,
        description=description,
        tags=tags,
        empik_url=empik_url,
    )


def scrape_category(
    category_id: int,
    category_slug: str,
    category_label: str,
    min_rating: float = MIN_RATING,
    min_ratings_count: int = MIN_RATINGS_COUNT,
) -> list[Book]:
    """Scrape one lubimyczytac.pl category and return qualifying Books."""
    session = _make_session()
    all_books: list[Book] = []
    seen_ids: set[str] = set()

    logger.info("Scraping category: %s (id=%d)", category_label, category_id)

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = _build_listing_url(category_id, category_slug, page)
        logger.info("Fetching listing page %d: %s", page, url)

        html = _fetch(session, url)
        if not html:
            logger.warning("Empty response on page %d, stopping category.", page)
            break
        _sleep()

        stubs = _parse_listing_page(html)
        if not stubs:
            logger.info("No book stubs on page %d — end of category.", page)
            break

        page_passed = 0
        page_failed = 0

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue
            seen_ids.add(book_id)

            book_html = _fetch(session, stub["url"])
            if not book_html:
                page_failed += 1
                _sleep()
                continue
            _sleep()

            book = _parse_book_page(
                book_html, stub, category_slug, category_label
            )
            if book:
                all_books.append(book)
                page_passed += 1
                logger.debug(
                    "  PASS: %s | %.1f/10 (%d ratings)",
                    book.title, book.rating, book.ratings_count,
                )
            else:
                page_failed += 1

        logger.info(
            "Page %d: %d passed filters, %d below threshold",
            page, page_passed, page_failed,
        )

        # Early exit: if every book on this page failed the filters,
        # pages are sorted by rating so no point continuing.
        if page_passed == 0 and page_failed == len(stubs):
            logger.info("Full page below threshold — stopping pagination early.")
            break

    logger.info(
        "Category '%s' done: %d books found.", category_label, len(all_books)
    )
    return all_books


def scrape_all_categories(
    categories: list[tuple[int, str, str]],
    min_rating: float = MIN_RATING,
    min_ratings_count: int = MIN_RATINGS_COUNT,
) -> list[Book]:
    """Scrape all configured categories and deduplicate by book_id."""
    all_books: list[Book] = []
    seen_ids: set[str] = set()

    for category_id, category_slug, category_label in categories:
        books = scrape_category(
            category_id, category_slug, category_label,
            min_rating, min_ratings_count,
        )
        for book in books:
            if book.book_id not in seen_ids:
                seen_ids.add(book.book_id)
                all_books.append(book)

    logger.info("Total unique qualifying books scraped: %d", len(all_books))
    return all_books
