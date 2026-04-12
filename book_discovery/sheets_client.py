"""
Google Sheets client.

Handles all read/write operations against the three sheet tabs:
  - books         : main database of discovered books
  - email_log     : log of each automated run
  - preferences   : user-configurable settings
"""

import logging
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials

from .models import Book
from .config import (
    Config,
    SHEET_BOOKS,
    SHEET_EMAIL_LOG,
    SHEET_PREFERENCES,
    BOOKS_HEADERS,
    EMAIL_LOG_HEADERS,
    PREFERENCES_HEADERS,
    DEFAULT_PREFERENCES,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _get_client(config: Config) -> gspread.Client:
    creds = Credentials.from_service_account_info(
        config.google_sheets_credentials, scopes=SCOPES
    )
    return gspread.authorize(creds)


def _open_spreadsheet(config: Config):
    client = _get_client(config)
    return client.open_by_key(config.google_sheet_id)


# ── Deduplication ──────────────────────────────────────────────────────────────

def get_existing_book_ids(config: Config) -> set[str]:
    """Return the set of all book_ids already stored in the books sheet."""
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)
    ids = ws.col_values(1)   # Column A
    result = set(ids[1:])    # Skip header row
    logger.info("Loaded %d existing book IDs from sheet.", len(result))
    return result


def get_emailed_book_ids(config: Config) -> set[str]:
    """Return book_ids that have already been included in an email digest."""
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)
    records = ws.get_all_records()
    emailed = {r["book_id"] for r in records if r.get("emailed_date")}
    logger.info("Found %d already-emailed book IDs.", len(emailed))
    return emailed


def get_already_read_ids(config: Config) -> set[str]:
    """Return book_ids marked already_read=TRUE by the user."""
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)
    records = ws.get_all_records()
    read_ids = {
        str(r["book_id"])
        for r in records
        if str(r.get("already_read", "")).upper() == "TRUE"
    }
    logger.info("Found %d already-read book IDs.", len(read_ids))
    return read_ids


# ── Writing ────────────────────────────────────────────────────────────────────

def append_books(books: list[Book], config: Config, emailed_date: str = "") -> None:
    """Append new book rows to the books sheet."""
    if not books:
        return
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)

    today = date.today().isoformat()
    rows = []
    for book in books:
        row = book.to_sheet_row(emailed_date=emailed_date)
        row[BOOKS_HEADERS.index("first_seen_date")] = today
        rows.append(row)

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info("Appended %d new books to sheet.", len(books))


def mark_books_emailed(book_ids: list[str], config: Config) -> None:
    """Set emailed_date for the given book_ids (batch update)."""
    if not book_ids:
        return
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)
    all_ids = ws.col_values(1)   # Column A (includes header)

    today = date.today().isoformat()
    emailed_col_index = BOOKS_HEADERS.index("emailed_date") + 1  # 1-based

    updates = []
    id_set = set(book_ids)
    for row_index, cell_id in enumerate(all_ids, start=1):
        if cell_id in id_set:
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_index, emailed_col_index),
                "values": [[today]],
            })

    if updates:
        ws.batch_update(updates)
        logger.info("Marked %d books as emailed (%s).", len(updates), today)


# ── Preferences ────────────────────────────────────────────────────────────────

def load_preferences(config: Config) -> dict:
    """Load key→value preferences from the preferences sheet."""
    try:
        sh = _open_spreadsheet(config)
        ws = sh.worksheet(SHEET_PREFERENCES)
        records = ws.get_all_records()
        prefs = {r["preference_key"]: r["preference_value"] for r in records}
        logger.info("Loaded preferences: %s", prefs)
        return prefs
    except Exception as e:
        logger.warning("Could not load preferences (%s). Using defaults.", e)
        return {}


# ── Run logging ────────────────────────────────────────────────────────────────

def log_run(
    config: Config,
    new_books_found: int,
    email_sent: bool,
    categories_scraped: list[str],
    books_in_db_total: int,
    error_log: str = "",
) -> None:
    """Append a run record to the email_log sheet."""
    try:
        sh = _open_spreadsheet(config)
        ws = sh.worksheet(SHEET_EMAIL_LOG)
        ws.append_row([
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            new_books_found,
            "TRUE" if email_sent else "FALSE",
            ", ".join(categories_scraped),
            books_in_db_total,
            error_log,
        ], value_input_option="USER_ENTERED")
        logger.info("Run logged to email_log sheet.")
    except Exception as e:
        logger.error("Failed to log run: %s", e)


def get_total_book_count(config: Config) -> int:
    """Return the number of books currently in the database."""
    sh = _open_spreadsheet(config)
    ws = sh.worksheet(SHEET_BOOKS)
    return max(0, ws.row_count - 1)  # Subtract header row


# ── One-time setup ─────────────────────────────────────────────────────────────

def setup_spreadsheet(config: Config) -> None:
    """Create the three sheet tabs with headers. Run once during initial setup."""
    sh = _open_spreadsheet(config)
    existing_titles = [ws.title for ws in sh.worksheets()]

    def ensure_worksheet(title: str, headers: list[str]) -> None:
        if title not in existing_titles:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
            logger.info("Created worksheet: %s", title)
        else:
            ws = sh.worksheet(title)
            logger.info("Worksheet already exists: %s", title)
        # Write headers only if row 1 is empty
        if not ws.row_values(1):
            ws.append_row(headers, value_input_option="USER_ENTERED")
            logger.info("Headers written to %s.", title)

    ensure_worksheet(SHEET_BOOKS, BOOKS_HEADERS)
    ensure_worksheet(SHEET_EMAIL_LOG, EMAIL_LOG_HEADERS)

    if SHEET_PREFERENCES not in existing_titles:
        ws = sh.add_worksheet(title=SHEET_PREFERENCES, rows=50, cols=3)
        ws.append_row(PREFERENCES_HEADERS, value_input_option="USER_ENTERED")
        ws.append_rows(DEFAULT_PREFERENCES, value_input_option="USER_ENTERED")
        logger.info("Preferences sheet created with defaults.")
    else:
        logger.info("Preferences sheet already exists.")

    logger.info("Spreadsheet setup complete.")
