"""
Email sender — builds and delivers the bi-weekly book digest via Gmail SMTP.
"""

import logging
import smtplib
import ssl
from collections import defaultdict
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import Book
from .config import Config

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _pick_book_of_fortnight(books: list[Book]) -> Book | None:
    """Return the book with the highest composite score (rating × log10(votes))."""
    if not books:
        return None
    return max(books, key=lambda b: b.composite_score)


def _group_by_category(books: list[Book]) -> dict[str, list[Book]]:
    """Group books by category_label, sorted by category size descending."""
    grouped: dict[str, list[Book]] = defaultdict(list)
    for book in books:
        grouped[book.category_label].append(book)
    return dict(
        sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
    )


def _next_run_date() -> str:
    """Return an approximate date for the next bi-weekly run (1st or 15th)."""
    today = date.today()
    if today.day < 15:
        next_run = date(today.year, today.month, 15)
    else:
        if today.month == 12:
            next_run = date(today.year + 1, 1, 1)
        else:
            next_run = date(today.year, today.month + 1, 1)
    return next_run.strftime("%-d %b %Y")


def build_email(
    new_books: list[Book],
    config: Config,
    total_in_db: int,
) -> tuple[str, str, str]:
    """Render both HTML and plain-text email bodies.

    Returns (subject, html_body, plain_body).
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)

    book_of_fortnight = _pick_book_of_fortnight(new_books)
    grouped_books = _group_by_category(new_books)

    context = {
        "run_date": date.today().strftime("%-d %b %Y"),
        "new_count": len(new_books),
        "total_in_db": total_in_db,
        "book_of_fortnight": book_of_fortnight,
        "grouped_books": grouped_books,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{config.google_sheet_id}",
        "sheet_id": config.google_sheet_id,
        "next_run_date": _next_run_date(),
    }

    html_body = env.get_template("email.html").render(**context)
    plain_body = env.get_template("email.txt").render(**context)

    subject = (
        f"📚 Nowe książki non-fiction "
        f"[{date.today().strftime('%-d %b %Y')}] "
        f"— {len(new_books)} nowych pozycji"
    )

    return subject, html_body, plain_body


def send_digest(
    new_books: list[Book],
    config: Config,
    total_in_db: int,
) -> None:
    """Build and send the digest email via Gmail SMTP."""
    subject, html_body, plain_body = build_email(new_books, config, total_in_db)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.gmail_user
    msg["To"] = config.recipient_email

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(config.gmail_user, config.gmail_app_password)
            server.sendmail(config.gmail_user, config.recipient_email, msg.as_string())
        logger.info(
            "Digest email sent to %s — %d new books.",
            config.recipient_email, len(new_books),
        )
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        raise
