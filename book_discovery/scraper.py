"""
Scraper for lubimyczytac.pl

Uses curl_cffi to impersonate Chrome's TLS fingerprint (plain requests returns 403).

Two-pass strategy per category:
  Pass 1 — sorted by publishDate desc (newest first)
    Stop as soon as any book's pub_date < cutoff_date.
    Catches brand-new books that may not yet have many ratings.

  Pass 2 — sorted by ratings desc (most popular first)
    Stop when listing-page rating drops below threshold.
    Catches popular recent books that accumulate high ratings quickly.

Results from both passes are merged and deduplicated by book_id.
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
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


def _fetch(session: cffi_requests.Session, url: str) -> Optional[str]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text
        logger.warning("HTTP %s for %s", resp.status_code, url)
        return None
    except Exception as e:
        logger.error("Request failed for %s: %s", url, e)
        return None


def extract_book_id(url: str) -> Optional[str]:
    """/ksiazka/4879823/sapiens → '4879823'"""
    m = re.search(r"/ksiazka/(\d+)/", url) or re.search(r"/ksiazka/(\d+)$", url)
    return m.group(1) if m else None


def _build_url(category_id: int, category_slug: str, page: int, order: str) -> str:
    """Build a category listing URL.

    lang[] kept as literal brackets — urllib would encode them to %5B%5D.
    order: 'publishDate' | 'ratings'
    """
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    qs = (
        f"listId=booksFilteredList"
        f"&listType=list"
        f"&orderBy={order}"
        f"&desc=1"
        f"&lang[]=pol"
        f"&page={page}"
        f"&paginatorType=url"
    )
    return f"{base}?{qs}"


def _build_url_simple(category_id: int, category_slug: str, page: int, order: str) -> str:
    """Fallback URL without AJAX params."""
    base = f"{BASE_URL}/ksiazki/k/{category_id}/{category_slug}"
    return f"{base}?orderBy={order}&desc=1&lang[]=pol&page={page}"


def _parse_rating_count(text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", text.replace("\u00a0", ""))
    return int(digits) if digits else None


def _parse_listing_page(html: str) -> list[dict]:
    """Return stubs: title, author, url, rating, ratings_count, pub_year."""
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
        snippet = body.get_text(separator=" ", strip=True)[:300] if body else html[200:600]
        logger.warning("No book cards found. HTML snippet: ...%s...", snippet)
        logger.debug("First 20 div classes: %s",
                     [" ".join(d.get("class", [])) for d in soup.find_all("div", class_=True, limit=20)])
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

    logger.debug("Parsed %d stubs", len(books))
    return books


def _parse_published_date(soup: BeautifulSoup) -> Optional[date]:
    """Extract publication date from a book page (OG meta → dt/dd → itemprop)."""
    og = soup.find("meta", {"property": "books:release_date"})
    if og and og.get("content"):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y"):
            try:
                return datetime.strptime(og["content"].strip(), fmt).date()
            except ValueError:
                continue

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
    """Build a Book from a parsed individual page. Returns None if below threshold."""
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
        logger.debug("Below threshold: %s | %.1f (%d)", stub["title"], rating, ratings_count)
        return None

    book_id = extract_book_id(stub["url"])
    if not book_id:
        return None

    cover_el = soup.find("meta", {"property": "og:image"})
    isbn_el = soup.find("meta", {"property": "books:isbn"})
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

    # Extract publisher from dt/dd pairs
    publisher: Optional[str] = None
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        if "wydawca" in label or "wydawnictwo" in label:
            dd = dt.find_next_sibling("dd")
            if dd:
                pub_link = dd.find("a")
                publisher = (pub_link or dd).get_text(strip=True) or None
            break

    return Book(
        book_id=book_id,
        title=stub["title"],
        author=stub["author"],
        category=category,
        category_label=category_label,
        rating=rating,
        ratings_count=ratings_count,
        url=stub["url"],
        isbn=isbn_el["content"] if isbn_el else None,
        cover_url=cover_el["content"] if cover_el else None,
        description=description,
        tags=tags,
        empik_url=(
            "https://www.empik.com/szukaj/produkt?q="
            + urllib.parse.quote(stub["title"])
        ),
        publisher=publisher,
    )


# ── Single-pass helpers ────────────────────────────────────────────────────────

def _scrape_pass_date(
    session: cffi_requests.Session,
    category_id: int,
    category_slug: str,
    category_label: str,
    min_rating: float,
    min_ratings_count: int,
    cutoff_date: date,
    seen_ids: set,
) -> list[Book]:
    """Pass 1: sort by publishDate desc. Stop at first book older than cutoff."""
    books: list[Book] = []
    label = f"{category_label}[date]"

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = _build_url(category_id, category_slug, page, "publishDate")
        logger.info("[%s] page %d → %s", label, page, url)

        html = _fetch(session, url)
        if not html:
            break
        _sleep()

        stubs = _parse_listing_page(html)
        if not stubs and page == 1:
            # Try fallback URL once
            fb = _build_url_simple(category_id, category_slug, page, "publishDate")
            logger.info("[%s] fallback URL: %s", label, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            break

        reached_cutoff = False

        for stub in stubs:
            # Cheap year check from listing card
            pub_year = stub.get("pub_year")
            if pub_year is not None and date(pub_year, 12, 31) < cutoff_date:
                logger.info("[%s] year %d before cutoff — stopping.", label, pub_year)
                reached_cutoff = True
                break

            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue

            # Rating pre-filter (skip HTTP request if clearly below)
            r, rc = stub.get("rating"), stub.get("ratings_count")
            if r is not None and rc is not None:
                if r < min_rating or rc < min_ratings_count:
                    continue

            book_html = _fetch(session, stub["url"])
            if not book_html:
                _sleep()
                continue
            _sleep()

            soup = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            if pub_date is not None and pub_date < cutoff_date:
                logger.info("[%s] %s pub=%s < cutoff — stopping.",
                            label, stub["title"], pub_date)
                reached_cutoff = True
                break

            book = _parse_book_page(soup, stub, category_slug, category_label,
                                    min_rating, min_ratings_count)
            if book:
                book.published_date = pub_date.isoformat() if pub_date else None
                books.append(book)
                seen_ids.add(book_id)
                logger.info("[%s] PASS: %s | %.1f/10 (%d) | pub: %s",
                            label, book.title, book.rating, book.ratings_count,
                            book.published_date or "?")

        logger.info("[%s] page %d done — %d total so far", label, page, len(books))

        if reached_cutoff:
            logger.info("[%s] Cutoff reached — stopping pagination.", label)
            break

    return books


def _scrape_pass_rating(
    session: cffi_requests.Session,
    category_id: int,
    category_slug: str,
    category_label: str,
    min_rating: float,
    min_ratings_count: int,
    cutoff_date: Optional[date],
    seen_ids: set,
) -> list[Book]:
    """Pass 2: sort by ratings desc. Stop when listing-page rating drops below threshold."""
    books: list[Book] = []
    label = f"{category_label}[rating]"

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = _build_url(category_id, category_slug, page, "ratings")
        logger.info("[%s] page %d → %s", label, page, url)

        html = _fetch(session, url)
        if not html:
            break
        _sleep()

        stubs = _parse_listing_page(html)
        if not stubs and page == 1:
            fb = _build_url_simple(category_id, category_slug, page, "ratings")
            logger.info("[%s] fallback URL: %s", label, fb)
            html2 = _fetch(session, fb)
            if html2:
                stubs = _parse_listing_page(html2)
                _sleep()

        if not stubs:
            break

        page_passed = 0
        page_fetched = 0  # individual pages actually fetched (not seen_ids / pre-filtered)

        for stub in stubs:
            book_id = extract_book_id(stub["url"])
            if not book_id or book_id in seen_ids:
                continue  # already captured by date pass — skip silently

            r, rc = stub.get("rating"), stub.get("ratings_count")
            if r is not None and rc is not None:
                if r < min_rating or rc < min_ratings_count:
                    continue  # pre-filtered by listing-page rating — no HTTP needed

            book_html = _fetch(session, stub["url"])
            if not book_html:
                _sleep()
                continue
            _sleep()
            page_fetched += 1

            soup = BeautifulSoup(book_html, "lxml")
            pub_date = _parse_published_date(soup)

            # Date filter (no early-exit — rating sort has no date monotonicity)
            if cutoff_date and pub_date is not None and pub_date < cutoff_date:
                logger.debug("[%s] Too old (%s): %s", label, pub_date, stub["title"])
                continue

            book = _parse_book_page(soup, stub, category_slug, category_label,
                                    min_rating, min_ratings_count)
            if book:
                book.published_date = pub_date.isoformat() if pub_date else None
                books.append(book)
                seen_ids.add(book_id)
                page_passed += 1
                logger.info("[%s] PASS: %s | %.1f/10 (%d) | pub: %s",
                            label, book.title, book.rating, book.ratings_count,
                            book.published_date or "?")

        logger.info("[%s] page %d — %d passed, %d fetched", label, page, page_passed, page_fetched)

        # Stop when we fetched individual pages but none qualified.
        # (seen_ids books are silently skipped and don't count here.)
        if page_fetched > 0 and page_passed == 0:
            logger.info("[%s] Fetched %d pages, 0 qualified — stopping.", label, page_fetched)
            break

        # Also stop if the entire page was pre-filtered away without any HTTP calls
        # (all stubs below rating threshold) — same as before.
        if page_fetched == 0 and not books:
            logger.info("[%s] Nothing to fetch on page %d — stopping.", label, page)
            break

    return books


# ── Public API ─────────────────────────────────────────────────────────────────

def scrape_category(
    category_id: int,
    category_slug: str,
    category_label: str,
    min_rating: float = MIN_RATING,
    min_ratings_count: int = MIN_RATINGS_COUNT,
    cutoff_date: Optional[date] = None,
) -> list[Book]:
    """Run both passes (date + rating) and return deduplicated results."""
    session = _make_session()
    seen_ids: set[str] = set()

    logger.info("=== Scraping: %s (id=%d) ===", category_label, category_id)

    # Pass 1: date-sorted (early exit at cutoff) — only meaningful with a date filter
    date_books: list[Book] = []
    if cutoff_date:
        date_books = _scrape_pass_date(
            session, category_id, category_slug, category_label,
            min_rating, min_ratings_count, cutoff_date, seen_ids,
        )
        logger.info("Date pass: %d books", len(date_books))

    # Pass 2: rating-sorted (early exit at rating threshold)
    rating_books = _scrape_pass_rating(
        session, category_id, category_slug, category_label,
        min_rating, min_ratings_count, cutoff_date, seen_ids,
    )
    logger.info("Rating pass: %d new books (not seen in date pass)", len(rating_books))

    all_books = date_books + rating_books
    logger.info("Category '%s' total: %d unique books", category_label, len(all_books))
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
        logger.info("Date filter: on or after %s", cutoff_date.isoformat())

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
