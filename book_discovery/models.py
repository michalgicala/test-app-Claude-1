from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Book:
    book_id: str                        # Numeric ID from lubimyczytac.pl URL
    title: str
    author: str
    category: str                       # Category slug (e.g. "literatura-faktu")
    category_label: str                 # Human-readable Polish label
    rating: float
    ratings_count: int
    url: str                            # Full lubimyczytac.pl URL
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None  # Scraped publisher description
    description_ai: Optional[str] = None  # Gemini-generated hook
    tags: list[str] = field(default_factory=list)
    empik_url: Optional[str] = None
    already_read: bool = False
    published_date: Optional[str] = None   # ISO date string (YYYY-MM-DD or YYYY-01-01)
    publisher: Optional[str] = None        # Publisher name extracted from book page

    @property
    def composite_score(self) -> float:
        """Rating × log10(ratings_count) — used to pick Book of the Fortnight."""
        import math
        return self.rating * math.log10(max(self.ratings_count, 1))

    def to_sheet_row(self, emailed_date: str = "") -> list:
        return [
            self.book_id,
            self.title,
            self.author,
            self.category_label,
            self.rating,
            self.ratings_count,
            self.url,
            self.isbn or "",
            self.cover_url or "",
            self.description or "",
            self.description_ai or "",
            ", ".join(self.tags),
            "",              # first_seen_date — filled by sheets_client
            emailed_date,
            self.empik_url or "",
            "FALSE",         # already_read — user fills this
            "",              # notes — user fills this
            self.publisher or "",  # col 17
        ]


@dataclass
class PremiereBook:
    """A book premiere from a target publisher, used for the premieres newsletter."""
    book_id: str
    title: str
    author: str
    publisher: str
    premiere_month: str   # YYYY-MM (e.g. "2026-01")
    url: str
    cover_url: Optional[str] = None
    isbn: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_sheet_row(self) -> list:
        """Produces a row matching PREMIERES_HEADERS in config.py."""
        return [
            self.book_id,
            self.title,
            self.author,
            self.publisher,
            self.premiere_month,
            self.url,
            self.cover_url or "",
            self.isbn or "",
            self.description or "",
            ", ".join(self.tags),
            "",   # emailed_month — filled after Apps Script sends the email
        ]
