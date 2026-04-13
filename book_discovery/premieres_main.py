"""
Orchestrator for the Premieres Newsletter.

Scrapes lubimyczytac.pl catalog for book premieres from selected publishers
and writes results to the 'premieres' tab in Google Sheets.
Email is sent via Google Apps Script (apps_script/PremiereNewsletter.gs).

Usage:
  # Current month:
  python -m book_discovery.premieres_main

  # Specific month:
  python -m book_discovery.premieres_main --year 2026 --month 3

  # Backfill Jan-Apr 2026 in a single run:
  python -m book_discovery.premieres_main --backfill
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from .config import (
    Config,
    SHEET_PREMIERES,
    PREMIERES_HEADERS,
    BACKFILL_MONTHS,
)
from .models import PremiereBook
from .premieres_scraper import scrape_premieres_for_month

# ── Logging ────────────────────────────────────────────────────────────────────

log_path = Path("premieres_run.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


# ── Sheet helpers ──────────────────────────────────────────────────────────────

def _open_sheet(config: Config):
    """Return (spreadsheet, worksheet) for the 'premieres' tab, creating it if needed."""
    creds  = Credentials.from_service_account_info(
        config.google_sheets_credentials, scopes=SCOPES
    )
    client = gspread.authorize(creds)
    sh     = client.open_by_key(config.google_sheet_id)

    existing = [ws.title for ws in sh.worksheets()]
    if SHEET_PREMIERES not in existing:
        ws = sh.add_worksheet(
            title=SHEET_PREMIERES, rows=5000, cols=len(PREMIERES_HEADERS)
        )
        ws.append_row(PREMIERES_HEADERS, value_input_option="USER_ENTERED")
        logger.info("Created '%s' worksheet with headers.", SHEET_PREMIERES)
    else:
        ws = sh.worksheet(SHEET_PREMIERES)
        logger.info("Using existing '%s' worksheet.", SHEET_PREMIERES)

    return ws


def _get_existing_ids(ws) -> set[str]:
    """Return all book_ids already present in the premieres sheet."""
    ids = ws.col_values(1)   # Column A = book_id
    return set(ids[1:])      # skip header


def _append_new(ws, books: list[PremiereBook], existing: set[str]) -> int:
    """Append books not yet in the sheet. Returns count of rows added."""
    rows = []
    for book in books:
        if book.book_id in existing:
            logger.debug("Skipping duplicate: %s (%s)", book.title, book.book_id)
            continue
        rows.append(book.to_sheet_row())
        existing.add(book.book_id)

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info("Appended %d premiere books to sheet.", len(rows))
    return len(rows)


# ── Per-month run ──────────────────────────────────────────────────────────────

def run_month(config: Config, year: int, month: int) -> int:
    """Scrape and persist premieres for one month. Returns count of new rows."""
    logger.info("=" * 60)
    logger.info("Premieres run: %04d-%02d", year, month)
    logger.info("=" * 60)

    ws       = _open_sheet(config)
    existing = _get_existing_ids(ws)

    books = scrape_premieres_for_month(year, month)
    added = _append_new(ws, books, existing)

    logger.info(
        "Finished %04d-%02d: %d new rows added (%d total found).",
        year, month, added, len(books),
    )
    return added


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape lubimyczytac.pl premieres and write to Google Sheets."
    )
    parser.add_argument("--year",     type=int, help="Year (default: current year)")
    parser.add_argument("--month",    type=int, help="Month 1–12 (default: current month)")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=f"Run for all backfill months: {BACKFILL_MONTHS}",
    )
    args = parser.parse_args()

    try:
        config = Config.from_env()
    except (KeyError, ValueError) as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    if args.backfill:
        logger.info("Backfill mode — processing %d months.", len(BACKFILL_MONTHS))
        for y, m in BACKFILL_MONTHS:
            run_month(config, y, m)
    else:
        today = date.today()
        year  = args.year  or today.year
        month = args.month or today.month
        run_month(config, year, month)

    logger.info("Premieres scraper finished.")


if __name__ == "__main__":
    main()
