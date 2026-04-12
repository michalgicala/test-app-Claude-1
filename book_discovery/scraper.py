"""
Scraper for lubimyczytac.pl

Uses curl_cffi to impersonate Chrome's TLS fingerprint (plain requests returns 403).
Fetches category listing pages, then individual book pages for additional data.

Key design choices:
- Rating is extracted from the listing page directly (avoids unnecessary page visits)
- Individual book pages are visited only for books that pass the rating threshold
- Publication date is extracted from individual pages and used for date filtering
- desc=1 ensures pages are sorted descending (most popular/recent first)
"""

import logging
import re
import time
import random
import urllib.parse
from datetime import date, timedelta, datetime
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
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
    if m:
        return m.group(1)
    # Also try without trailing slash
    m = re.search(r"/ksiazka/(\d+)$", url)
    return m.group(1) if m else None


def _build_listing_url(category_id: int, category_slug: str, page: int) -> str:
    """Build the category listing URL with correct parameters.

    Uses manual string building to preserve literal lang[] (not URL-encoded).
    desc=1 ensures descending order (highest rating/most popular first).
    """
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    # Build query string manually to keep literal [] in lang[]
    qs = (
        f"listId=booksFilteredList"
        f"&listType=list"
        f"&orderBy=ratings"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )
    return f"{base}?{qs}"


def _build_listing_url_simple(category_id: int, category_slug: str, page: int) -> str:
    """Simpler category URL without AJAX params — fallback for categories
    that don't respond to the AJAX endpoint."""
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    qs = f"orderBy=ratings&desc=1&lang[]=pol&page={page}"
    return f"{base}?{qs}"


def _parse_rating_count(text: str) -> Optional[int]:
    """Parse a ratings count string like '1 234 ocen' or '(342)' → int."""
    if not text:
        return None
    # Remove anything that isn't a digit or space
    digits = re.sub(r"[^\d\s]", "", text).strip()
    # Remove spaces (used as thousands separator in Polish)
    digits = digits.replace(" ", "").replace("\u00a0", "")
    if digits:
        try:
            return int(digits)
        except ValueError:
            pass
    return None


def _parse_listing_page(html: str) -> list[dict]:
    """Parse a category listing page.

    Returns list of dicts with keys: title, author, url, rating, ratings_count.
    Rating and ratings_count may be None if not found on the listing page.
    """
    soup = BeautifulSoup(html, "lxml")
    books = []

    # Try multiple selectors in order of likelihood
    cards = (
        soup.select("div.authorAllBooks__single")
        or soup.select("div.listBook__item")
        or soup.select("div.book-item")
        or soup.select("li.categoryBooksList__item")
        or soup.select(".booksList .row > div[class*='col']")
    )

    if not cards:
        # Log a HTML snippet to help diagnose selector issues
        body = soup.find("body")
        snippet = (body.get_text(separator=" ", strip=True)[:300] if body
                   else html[200:600])
        logger.warning(
            "No book cards found on listing page. "
            "HTML snippet: ...%s...", snippet
        )
        # Also log the first few class names found
        all_divs = soup.find_all("div", class_=True, limit=20)
        class_names = [" ".join(d.get("class", [])) for d in all_divs]
        logger.debug("First 20 div classes on page: %s", class_names)
        return []

    logger.debug("Found %d book cards using selector", len(cards))

    for card in cards:
        # Title + URL
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

        # Author
        author_el = (
            card.select_one(".authorAllBooks__singleTextAuthor a")
            or card.select_one("a[href*='/autor/']")
            or card.select_one(".book-author a")
        )
        author = author_el.get_text(strip=True) if author_el else "Nieznany autor"

        # Rating from listing page (avoids visiting individual pages for non-qualifying books)
        rating: Optional[float] = None
        ratings_count: Optional[int] = None

        rating_el = card.select_one(".listLibrary__ratingStarsNumber")
        if rating_el:
            try:
                rating = float(rating_el.get_text(strip=True).replace(",", "."))
            except ValueError:
                pass

        count_el = (
            card.select_one(".listLibrary__ratingAll")
            or card.select_one(".ratingCount")
        )
        if count_el:
            ratings_count = _parse_rating_count(count_el.get_text(strip=True))

        books.append({
            "title": title,
            "author": author,
            "url": url,
            "rating": rating,
            "ratings_count": ratings_count,
        })

    logger.debug("Parsed %d book stubs from listing page", len(books))
    return books


def _parse_published_date(soup: BeautifulSoup) -> Optional[date]:
    """Try to extract the publication date from a book page.

    Returns a date object or None if unavailable.
    """
    # 1. Open Graph meta: books:release_date (most reliable, ISO format)
    og_date = soup.find("meta", {"property": "books:release_date"})
    if og_date and og_date.get("content"):
        content = og_date["content"].strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y"):
            try:
                return datetime.strptime(content, fmt).date()
            except ValueError:
                continue

    # 2. dt/dd pairs — look for "Data wydania" (exact date) first, then "Rok wydania" (year)
    details_dl = soup.find("dl", class_=re.compile(r"book", re.I))
    if not details_dl:
        # Try any dl on page
        details_dl = soup.find("dl")

    if details_dl:
        dts = details_dl.find_all("dt")
        for dt in dts:
            label = dt.get_text(strip=True).lower()
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            value = dd.get_text(strip=True)

            if "data wydania" in label or "data premiery" in label:
                # Polish date: DD.MM.YYYY or YYYY-MM-DD
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
                m = re.search(r"(\d{4})", value)
                if m:
                    try:
                        # Year only — use January 1 of that year
                        return date(int(m.group(1)), 1, 1)
                    except ValueError:
                        pass

    # 3. Span with itemprop="datePublished"
    itemprop_el = soup.find(attrs={"itemprop": "datePublished"})
    if itemprop_el:
        content = (itemprop_el.get("content") or itemprop_el.get_text(strip=True))
        m = re.search(r"(\d{4})", content)
        if m:
            try:
                return date(int(m.group(1)), 1, 1)
            except ValueError:
                pass

    return None


def _parse_book_page(
    html: str,
    stub: dict,
    category: str,
    category_label: str,
    min_rating: float,
    min_ratings_count: int,
    cutoff_date: Optional[date] = None,
) -> Optional[Book]:
    """Parse an individual book page and return a Book object if it qualifies."""
    soup = BeautifulSoup(html, "lxml")

    # --- Rating: prefer value already known from listing page ---
    rating = stub.get("rating")
    ratings_count = stub.get("ratings_count")

    # Fallback to Open Graph meta tags on the individual page
    if rating is None:
        rating_el = soup.find("meta", {"property": "books:rating:value"})
        if rating_el and rating_el.get("content"):
            try:
                rating = float(rating_el["content"].replace(",", "."))
            except (KeyError, ValueError):
                pass

    if ratings_count is None:
        count_el = soup.find("meta", {"property": "books:rating:count"})
        if count_el and count_el.get("content"):
            try:
                ratings_count = int(count_el["content"])
            except (KeyError, ValueError):
                pass

    if rating is None or ratings_count is None:
        logger.debug("No rating data found for %s", stub["url"])
        return None

    # --- Apply rating filters ---
    if rating < min_rating or ratings_count < min_ratings_count:
        logger.debug(
            "Below threshold: %s | %.1f/10 (%d ratings)",
            stub["title"], rating, ratings_count,
        )
        return None

    # --- Publication date filter ---
    published_date = _parse_published_date(soup)
    if cutoff_date is not None and published_date is not None:
        if published_date < cutoff_date:
            logger.debug(
                "Too old (%s): %s", published_date.isoformat(), stub["title"]
            )
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
    desc_el = (
        soup.select_one("div.collapse-content")
        or soup.select_one("div#book-description")
        or soup.select_one(".book-description")
        or soup.select_one("div[itemprop='description']")
    )
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
        published_date=published_date.isoformat() if published_date else None,
    )


def scrape_category(
    category_id: int,
    category_slug: str,
    category_label: str,
    min_rating: float = MIN_RATING,
    min_ratings_count: int = MIN_RATINGS_COUNT,
    cutoff_date: Optional[date] = None,
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

        # If AJAX URL returned nothing, try the simpler URL as fallback
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
        page_failed = 0
        page_old = 0  # books filtered out by date

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue
            seen_ids.add(book_id)

            # Pre-filter by rating if we already have listing-page data
            listing_rating = stub.get("rating")
            listing_count = stub.get("ratings_count")

            if listing_rating is not None and listing_count is not None:
                if listing_rating < min_rating or listing_count < min_ratings_count:
                    page_failed += 1
                    logger.debug(
                        "  PRE-FILTER: %s | %.1f/10 (%d ratings)",
                        stub["title"], listing_rating, listing_count,
                    )
                    continue

            # Fetch individual book page for full data + date check
            book_html = _fetch(session, stub["url"])
            if not book_html:
                page_failed += 1
                _sleep()
                continue
            _sleep()

            book = _parse_book_page(
                book_html, stub, category_slug, category_label,
                min_rating, min_ratings_count, cutoff_date,
            )
            if book:
                all_books.append(book)
                page_passed += 1
                logger.info(
                    "  PASS: %s | %.1f/10 (%d ratings)%s",
                    book.title, book.rating, book.ratings_count,
                    f" | pub: {book.published_date}" if book.published_date else "",
                )
            else:
                # Determine why it failed for better logging
                page_failed += 1

        logger.info(
            "Page %d: %d passed, %d rejected (rating/data), %d too old",
            page, page_passed, page_failed, page_old,
        )

        # Early exit: if every book on this page failed and we have listing ratings,
        # pages are sorted by rating so no point continuing.
        if page_passed == 0 and page_failed > 0 and listing_rating is not None:
            if listing_rating < min_rating:
                logger.info("Rating dropped below threshold — stopping pagination early.")
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
        logger.info("Date filter: books published on or after %s", cutoff_date.isoformat())

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
