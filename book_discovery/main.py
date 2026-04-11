"""
Main orchestrator for the book discovery pipeline.

Flow:
  1. Load config from environment variables
  2. Load preferences from Google Sheets
  3. Get already-known book IDs from Google Sheets
  4. Scrape lubimyczytac.pl categories
  5. Filter to only truly new books (not in DB, not already emailed)
  6. Generate AI descriptions via Gemini
  7. Send digest email
  8. Append new books to the sheet + mark as emailed
  9. Log the run
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path

from .config import Config, CATEGORIES
from .scraper import scrape_all_categories
from .sheets_client import (
    get_existing_book_ids,
    get_already_read_ids,
    append_books,
    mark_books_emailed,
    log_run,
    get_total_book_count,
    load_preferences,
)
from .ai_descriptions import enrich_books
from .email_sender import send_digest

# ── Logging setup ──────────────────────────────────────────────────────────────
log_path = Path("run.log")
handlers = [
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(log_path, encoding="utf-8"),
]
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    handlers=handlers,
)
logger = logging.getLogger(__name__)


def _apply_preferences(config: Config, prefs: dict) -> Config:
    """Override config values with user preferences from the sheet."""
    try:
        config.min_rating = float(prefs.get("min_rating", config.min_rating))
    except ValueError:
        pass
    try:
        config.min_ratings_count = int(
            prefs.get("min_ratings_count", config.min_ratings_count)
        )
    except ValueError:
        pass

    # Recipient email override
    override_email = prefs.get("recipient_email", "").strip()
    if override_email:
        config.recipient_email = override_email

    return config


def main() -> None:
    logger.info("=" * 60)
    logger.info("Book Discovery run started: %s", date.today().isoformat())
    logger.info("=" * 60)

    # 1. Load config
    try:
        config = Config.from_env()
    except (KeyError, ValueError) as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    email_sent = False
    error_log = ""
    new_books_found = 0

    try:
        # 2. Load preferences
        prefs = load_preferences(config)
        config = _apply_preferences(config, prefs)
        logger.info(
            "Thresholds: rating >= %.1f, votes >= %d",
            config.min_rating, config.min_ratings_count,
        )

        # 3. Load known book IDs (already in DB) and already-read IDs
        existing_ids = get_existing_book_ids(config)
        already_read_ids = get_already_read_ids(config)

        # 4. Scrape categories
        scraped_books = scrape_all_categories(
            categories=config.categories,
            min_rating=config.min_rating,
            min_ratings_count=config.min_ratings_count,
        )

        # 5. Filter to genuinely new, not-already-read books
        new_books = [
            b for b in scraped_books
            if b.book_id not in existing_ids
            and b.book_id not in already_read_ids
        ]
        new_books_found = len(new_books)
        logger.info(
            "New books after deduplication: %d (scraped %d total)",
            new_books_found, len(scraped_books),
        )

        if not new_books:
            logger.info("No new books found. Skipping email.")
        else:
            # 6. Enrich with AI descriptions
            new_books = enrich_books(new_books, config.gemini_api_key)

            # 7. Append new books to sheet first (so DB is updated even if email fails)
            append_books(new_books, config)

            # 8. Send digest
            total_in_db = get_total_book_count(config)
            send_digest(new_books, config, total_in_db)
            email_sent = True

            # 9. Mark books as emailed
            mark_books_emailed(
                [b.book_id for b in new_books], config
            )

    except Exception as e:
        logger.exception("Unexpected error during run: %s", e)
        error_log = str(e)
    finally:
        # Always log the run
        try:
            log_run(
                config=config,
                new_books_found=new_books_found,
                email_sent=email_sent,
                categories_scraped=[label for _, _, label in CATEGORIES],
                books_in_db_total=0,   # Approximate — not critical
                error_log=error_log,
            )
        except Exception as log_err:
            logger.error("Failed to write run log: %s", log_err)

    logger.info("Run complete. Email sent: %s", email_sent)


if __name__ == "__main__":
    main()
