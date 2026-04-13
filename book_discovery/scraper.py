"""
Scraper for lubimyczytac.pl

Uses curl_cffi to impersonate Chrome's TLS fingerprint (plain requests returns 403).

Pagination strategy:
- Pages are sorted by publication date descending (newest first).
- As soon as ANY book's publication date falls before the cutoff, we stop — no
  point checking further pages because all subsequent books will be even older.
- Individual book pages are fetched only for books that pass the rating pre-filter
  from the listing page.
"""

import logging
import re
import time
import random
import urllib.parse
from datetime import date, datetime
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
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
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
    m = re.search(r"/ksiazka/(\d+)/", url) or re.search(r"/ksiazka/(\d+)$", url)
    return m.group(1) if m else None


def _build_listing_url(category_id: int, category_slug: str, page: int) -> str:
    """Category listing URL sorted by publication date descending (newest first).

    lang[] is kept as literal brackets — urllib would encode them to %5B%5D
    which some servers ignore.
    """
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    qs = (
        f"listId=booksFilteredList"
        f"&listType=list"
        f"&orderBy=publishDate"   # Sort by publication date
        f"&desc=1"                # Newest first
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )
    return f"{base}?{qs}"


def _build_listing_url_simple(category_id: int, category_slug: str, page: int) -> str:
    """Fallback URL without AJAX params, for categories that don't respond to the
    AJAX endpoint."""
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    qs = f"orderBy=publishDate&desc=1&lang[]=pol&page={page}"
    return f"{base}?{qs}"


def _parse_rating_count(text: str) -> Optional[int]:
    """Parse '1 234 ocen' or '(342)' → int."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text.replace("\u00a0", ""))
    return int(digits) if digits else None


def _parse_listing_page(html: str) -> list[dict]:
    """Parse a category listing page.

    Returns list of dicts: title, author, url, rating, ratings_count, pub_year.
    pub_year is extracted when the listing card shows it; otherwise None.
    """
    soup = BeautifulSoup(html, "lxml")

    cards = (
        soup.select("div.authorAllBooks__single")
        or soup.select("div.listBook__item")
        or soup.select("div.book-item")
        or soup.select("li.categoryBooksList__item")
        or soup.select(".booksList .row > div[class*='col']")
    )

    if not cards:
        body = soup.find("body")
        snippet = (body.get_text(separator=" ", strip=True)[:300] if body
                   else html[200:600])
        logger.warning("No book cards found on listing page. Snippet: ...%s...", snippet)
        all_divs = soup.find_all("div", class_=True, limit=20)
        logger.debug("First 20 div classes: %s",
                     [" ".join(d.get("class", [])) for d in all_divs])
        return []

    books = []
    for card in cards:
        title_el = (
            card.select_one("a.authorAllBooks__singleTextTitle")
            or card.select_one("a[href*='/ksiazka/']")
            or card.select_one("a.title")
        )
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        url = href if href.startswith("http") else BASE_URL + href

        author_el = (
            card.select_one(".authorAllBooks__singleTextAuthor a")
            or card.select_one("a[href*='/autor/']")
        )
        author = author_el.get_text(strip=True) if author_el else "Nieznany autor"

        # Rating (may not always be present when sorting by date)
        rating: Optional[float] = None
        rating_el = card.select_one(".listLibrary__ratingStarsNumber")
        if rating_el:
            try:
                rating = float(rating_el.get_text(strip=True).replace(",", "."))
            except ValueError:
                pass

        ratings_count: Optional[int] = None
        count_el = card.select_one(".listLibrary__ratingAll") or card.select_one(".ratingCount")
        if count_el:
            ratings_count = _parse_rating_count(count_el.get_text(strip=True))

        # Publication year from listing card (used for cheap early-exit check)
        pub_year: Optional[int] = None
        year_el = (
            card.select_one(".listLibrary__year")
            or card.select_one("[class*='year']")
            or card.select_one("[data-year]")
        )
        if year_el:
            m = re.search(r"(20\d{2}|19\d{2})", year_el.get_text(strip=True))
            if m:
                pub_year = int(m.group(1))

        books.append({
            "title": title,
            "author": author,
            "url": url,
            "rating": rating,
            "ratings_count": ratings_count,
            "pub_year": pub_year,
        })

    logger.debug("Parsed %d book stubs from listing page", len(books))
    return books


def _parse_published_date(soup: BeautifulSoup) -> Optional[date]:
    """Extract publication date from a book page.

    Tries (in order):
    1. OG meta  books:release_date  (ISO format)
    2. dt/dd    Data wydania        (DD.MM.YYYY)
    3. dt/dd    Rok wydania         (YYYY → Jan 1 of that year)
    4. itemprop datePublished
    """
    # 1. Open Graph
    og = soup.find("meta", {"property": "books:release_date"})
    if og and og.get("content"):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y"):
            try:
                return datetime.strptime(og["content"].strip(), fmt).date()
            except ValueError:
                continue

    # 2 & 3. dt/dd pairs
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        value = dd.get_text(strip=True)

        if "data wydania" in label or "data premiery" in label:
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
            if m:
                try:
                    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass

        if "rok wydania" in label or "rok premiery" in label:
            m = re.search(r"(20\d{2}|19\d{2})", value)
            if m:
                try:
                    return date(int(m.group(1)), 1, 1)
                except ValueError:
                    pass

    # 4. itemprop
    el = soup.find(attrs={"itemprop": "datePublished"})
    if el:
        content = el.get("content") or el.get_text(strip=True)
        m = re.search(r"(20\d{2}|19\d{2})", content)
        if m:
            try:
                return date(int(m.group(1)), 1, 1)
            except ValueError:
                pass

    return None


def _parse_book_page(
    soup: BeautifulSoup,
    stub: dict,
    category: str,
    category_label: str,
    min_rating: float,
    min_ratings_count: int,
) -> Optional[Book]:
    """Build a Book from an already-parsed individual book page.

    Does NOT apply the date filter — caller handles that so it can also
    use the date for the early-exit decision.
    Returns None if rating data is missing or below threshold.
    """
    # Rating: prefer listing-page value, fall back to OG meta
    rating = stub.get("rating")
    ratings_count = stub.get("ratings_count")

    if rating is None:
        el = soup.find("meta", {"property": "books:rating:value"})
        if el and el.get("content"):
            try:
                rating = float(el["content"].replace(",", "."))
            except ValueError:
                pass

    if ratings_count is None:
        el = soup.find("meta", {"property": "books:rating:count"})
        if el and el.get("content"):
            try:
                ratings_count = int(el["content"])
            except ValueError:
                pass

    if rating is None or ratings_count is None:
        logger.debug("No rating data for %s", stub["url"])
        return None

    if rating < min_rating or ratings_count < min_ratings_count:
        logger.debug("Below threshold: %s | %.1f/10 (%d)", stub["title"], rating, ratings_count)
        return None

    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    cover_el = soup.find("meta", {"property": "og:image"})
    cover_url = cover_el["content"] if cover_el else None

    isbn_el = soup.find("meta", {"property": "books:isbn"})
    isbn = isbn_el["content"] if isbn_el else None

    desc_el = (
        soup.select_one("div.collapse-content")
        or soup.select_one("div#book-description")
        or soup.select_one(".book-description")
        or soup.select_one("div[itemprop='description']")
    )
    description = ""
    if desc_el:
        description = re.sub(r"\s+", " ", desc_el.get_text(separator=" ", strip=True)).strip()

    tag_els = soup.select("a[href*='/ksiazki/t/']")
    tags = list(dict.fromkeys(el.get_text(strip=True) for el in tag_els))[:10]

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
    cutoff_date: Optional[date] = None,
) -> list[Book]:
    """Scrape one category, stopping as soon as any book predates the cutoff.

    Pages are sorted by publication date descending, so the first book whose
    pub_date < cutoff_date means all remaining books (this page and beyond)
    are also before the cutoff.
    """
    session = _make_session()
    all_books: list[Book] = []
    seen_ids: set[str] = set()

    logger.info("Scraping category: %s (id=%d)", category_label, category_id)

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = _build_listing_url(category_id, category_slug, page)
        logger.info("Fetching listing page %d: %s", page, url)

        html = _fetch(session, url)
        if not html:
            logger.warning("Empty response on page %d — stopping.", page)
            break
        _sleep()

        stubs = _parse_listing_page(html)

        # If AJAX URL returned no cards, try the simpler fallback once
        if not stubs and page == 1:
            fallback_url = _build_listing_url_simple(category_id, category_slug, page)
            logger.info("Trying fallback URL: %s", fallback_url)
            html2 = _fetch(session, fallback_url)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            logger.info("No book stubs on page %d — end of category.", page)
            break

        page_passed = 0
        page_skipped_rating = 0
        reached_cutoff = False  # set True → break both loops

        for stub in stubs:
            # ── Cheap date check from listing card (no HTTP request needed) ──
            pub_year = stub.get("pub_year")
            if cutoff_date and pub_year is not None:
                # If the book's latest possible date (Dec 31) is still before
                # the cutoff, we can stop without visiting the individual page.
                if date(pub_year, 12, 31) < cutoff_date:
                    logger.info(
                        "Listing-page year %d is before cutoff %s — stopping.",
                        pub_year, cutoff_date,
                    )
                    reached_cutoff = True
                    break

            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue
            seen_ids.add(book_id)

            # ── Rating pre-filter (skip individual page visit if clearly below) ──
            listing_rating = stub.get("rating")
            listing_count = stub.get("ratings_count")
            if listing_rating is not None and listing_count is not None:
                if listing_rating < min_rating or listing_count < min_ratings_count:
                    page_skipped_rating += 1
                    continue

            # ── Fetch individual book page ──
            book_html = _fetch(session, stub["url"])
            if not book_html:
                page_skipped_rating += 1
                _sleep()
                continue
            _sleep()

            soup = BeautifulSoup(book_html, "lxml")

            # ── Exact date check (most reliable) ──
            pub_date = _parse_published_date(soup)
            if cutoff_date and pub_date is not None and pub_date < cutoff_date:
                logger.info(
                    "Date cutoff reached: %s published %s (cutoff %s) — stopping.",
                    stub["title"], pub_date, cutoff_date,
                )
                reached_cutoff = True
                break

            # ── Full parse ──
            book = _parse_book_page(
                soup, stub, category_slug, category_label,
                min_rating, min_ratings_count,
            )
            if book:
                book.published_date = pub_date.isoformat() if pub_date else None
                all_books.append(book)
                page_passed += 1
                logger.info(
                    "  PASS: %s | %.1f/10 (%d ratings)%s",
                    book.title, book.rating, book.ratings_count,
                    f" | pub: {book.published_date}" if book.published_date else "",
                )
            else:
                page_skipped_rating += 1

        logger.info(
            "Page %d: %d passed, %d skipped (rating/data)",
            page, page_passed, page_skipped_rating,
        )

        if reached_cutoff:
            logger.info("Cutoff date reached — stopping pagination for '%s'.", category_label)
            break

    logger.info("Category '%s' done: %d books found.", category_label, len(all_books))
    return all_books


def scrape_all_categories(
    categories: list[tuple[int, str, str]],
    min_rating: float = MIN_RATING,
    min_ratings_count: int = MIN_RATINGS_COUNT,
    cutoff_date: Optional[date] = None,
) -> list[Book]:
    """Scrape all configured categories and deduplicate by book_id."""
    all_books: list[Book] = []
    seen_ids: set[str] = set()

    if cutoff_date:
        logger.info("Date filter active: books published on or after %s", cutoff_date.isoformat())

    for category_id, category_slug, category_label in categories:
        books = scrape_category(
            category_id, category_slug, category_label,
            min_rating, min_ratings_count, cutoff_date,
        )
        for book in books:
            if book.book_id not in seen_ids:
                seen_ids.add(book.book_id)
                all_books.append(book)

    logger.info("Total unique qualifying books scraped: %d", len(all_books))
    return all_books
