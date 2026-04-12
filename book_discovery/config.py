# -*- coding: utf-8 -*-
import os
import json
from dataclasses import dataclass, field

BASE_URL = "https://lubimyczytac.pl"

# (category_id, url_slug, human_label)
CATEGORIES = [
    (46,  "literatura-faktu",           "Literatura faktu / Reportaż"),
    (5,   "biografie-i-autobiografie",  "Biografie i Autobiografie"),
    (10,  "historia",                   "Historia"),
    (84,  "psychologia",                "Psychologia"),
    (31,  "literatura-popularnonaukowa","Literatura popularnonaukowa"),
]

# Scraping thresholds
MIN_RATING: float = 7.0
MIN_RATINGS_COUNT: int = 20
MAX_PAGES_PER_CATEGORY: int = 10

# Between HTTP requests (seconds)
REQUEST_DELAY_MIN: float = 2.0
REQUEST_DELAY_MAX: float = 4.0

# Publication date filter windows (days back from today)
# Books older than this are excluded from results
PUBLICATION_WINDOW_DAYS: int = 14        # Regular run: last 2 weeks
FIRST_RUN_PUBLICATION_WINDOW_DAYS: int = 60  # First run: last 2 months

# Gemini model — free tier (1 000 req/day on gemini-2.0-flash-lite)
GEMINI_MODEL: str = "gemini-2.0-flash-lite"
GEMINI_MAX_BOOKS_PER_RUN: int = 50
GEMINI_DELAY_SECONDS: float = 0.5

# Sheet tab names
SHEET_BOOKS = "books"
SHEET_EMAIL_LOG = "email_log"
SHEET_PREFERENCES = "preferences"

# Column headers for each sheet tab
BOOKS_HEADERS = [
    "book_id", "title", "author", "category", "rating", "ratings_count",
    "url", "isbn", "cover_url", "description", "description_ai", "tags",
    "first_seen_date", "emailed_date", "empik_url", "already_read", "notes",
]

EMAIL_LOG_HEADERS = [
    "run_date", "new_books_found", "email_sent", "categories_scraped",
    "books_in_db_total", "error_log",
]

PREFERENCES_HEADERS = ["preference_key", "preference_value", "notes"]

DEFAULT_PREFERENCES = [
    ["min_rating",        "7.0", "Minimalna średnia ocena (0-10)"],
    ["min_ratings_count", "20",  "Minimalna liczba ocen"],
]


@dataclass
class Config:
    google_sheets_credentials: dict
    google_sheet_id: str
    gemini_api_key: str
    min_rating: float = MIN_RATING
    min_ratings_count: int = MIN_RATINGS_COUNT
    categories: list = field(default_factory=lambda: list(CATEGORIES))

    @classmethod
    def from_env(cls) -> "Config":
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON", "{}")
        try:
            creds = json.loads(creds_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"GOOGLE_SHEETS_CREDENTIALS_JSON is not valid JSON: {e}")

        return cls(
            google_sheets_credentials=creds,
            google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        )
