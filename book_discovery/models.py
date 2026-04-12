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
        ]
