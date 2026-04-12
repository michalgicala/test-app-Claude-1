"""
Gemini API integration for generating Polish "why read this" blurbs.

Uses gemini-2.0-flash-lite (free tier: 1,000 requests/day).
Falls back gracefully to the scraped description if the API fails.
"""

import logging
import time

import google.generativeai as genai

from .models import Book
from .config import GEMINI_MODEL, GEMINI_MAX_BOOKS_PER_RUN, GEMINI_DELAY_SECONDS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Jesteś ekspertem od rekomendacji książek. Na podstawie podanych informacji "
    "o książce napisz 2-3 zdania po polsku, które zachęcą czytelnika do jej "
    "przeczytania. Skup się na tym, co czytelnik zyska lub czego się dowie. "
    "Pisz konkretnie i entuzjastycznie, ale bez przesady. Nie zaczynaj od słów "
    "'Ta książka' ani nie używaj frazesów marketingowych."
)


def _truncate_description(description: str, max_chars: int = 300) -> str:
    """Return a clean truncated fallback description."""
    if not description:
        return ""
    text = description[:max_chars].strip()
    if len(description) > max_chars:
        text = text.rsplit(" ", 1)[0] + "…"
    return text


def generate_hook(book: Book, api_key: str) -> str:
    """Call Gemini to generate a 2-3 sentence Polish hook for one book.

    Returns the AI-generated text, or a truncated scraped description on failure.
    """
    fallback = _truncate_description(book.description)

    if not api_key:
        logger.debug("No Gemini API key — using fallback description.")
        return fallback

    prompt = (
        f"Tytuł: {book.title}\n"
        f"Autor: {book.author}\n"
        f"Kategoria: {book.category_label}\n"
        f"Ocena: {book.rating}/10 ({book.ratings_count} ocen)\n"
        f"Tagi: {', '.join(book.tags[:5])}\n"
        f"Opis: {(book.description or '')[:500]}\n\n"
        "Napisz krótką rekomendację (2-3 zdania) po polsku:"
    )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(prompt)
        text = response.text.strip()
        logger.debug("Gemini hook generated for: %s", book.title)
        return text
    except Exception as e:
        logger.warning("Gemini API error for '%s': %s — using fallback.", book.title, e)
        return fallback


def enrich_books(books: list[Book], api_key: str) -> list[Book]:
    """Add AI-generated descriptions to a list of books (in-place).

    Caps at GEMINI_MAX_BOOKS_PER_RUN to respect free-tier limits.
    """
    to_process = books[:GEMINI_MAX_BOOKS_PER_RUN]
    skipped = len(books) - len(to_process)

    if skipped:
        logger.warning(
            "Capping AI descriptions at %d books (%d skipped — will use fallback).",
            GEMINI_MAX_BOOKS_PER_RUN, skipped,
        )

    for i, book in enumerate(to_process):
        book.description_ai = generate_hook(book, api_key)
        if i < len(to_process) - 1:
            time.sleep(GEMINI_DELAY_SECONDS)

    # For books beyond the cap, use truncated scraped description
    for book in books[GEMINI_MAX_BOOKS_PER_RUN:]:
        book.description_ai = _truncate_description(book.description)

    return books
