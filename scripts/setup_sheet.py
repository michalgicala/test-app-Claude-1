"""
One-time setup script — creates the three Google Sheets tabs with headers.

Run this once after:
  1. Creating your Google Cloud service account
  2. Sharing the spreadsheet with the service account email
  3. Setting environment variables (or creating a .env file)

Usage:
    # With a .env file:
    python -m dotenv -f .env run python scripts/setup_sheet.py

    # With env vars already set:
    python scripts/setup_sheet.py
"""

import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("Loaded .env file.")
except ImportError:
    print("python-dotenv not installed — reading env vars directly.")

from book_discovery.config import Config
from book_discovery.sheets_client import setup_spreadsheet
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

if __name__ == "__main__":
    print("Setting up Google Sheets database...")
    try:
        config = Config.from_env()
    except (KeyError, ValueError) as e:
        print(f"ERROR: Missing environment variable — {e}")
        print("Make sure all required env vars are set (see .env.example).")
        sys.exit(1)

    setup_spreadsheet(config)
    print()
    print("✓ Setup complete!")
    print(f"  Open your spreadsheet: https://docs.google.com/spreadsheets/d/{config.google_sheet_id}")
    print()
    print("Next step: Run a test scan:")
    print("  python -m book_discovery.main")
