from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Author, Book


def author_display_name(author: Author) -> str:
    parts = [author.last_name, author.first_name]
    if author.middle_name:
        parts.append(author.middle_name)
    return " ".join(parts)


def book_authors_line(book: Book) -> str:
    if not book.authors:
        return "—"
    return ", ".join(author_display_name(a) for a in book.authors)


def book_categories_line(book: Book) -> str:
    if not book.categories:
        return "—"
    return ", ".join(c.name for c in book.categories)


def user_display_name(user) -> str:
    parts = [user.last_name, user.first_name]
    if user.middle_name:
        parts.append(user.middle_name)
    return " ".join(parts)


DEFAULT_BOOK_COVER_URL = "/static/images/books/imageholder.jpg"


def book_cover_src(cover_url: str | None) -> str:
    if not cover_url:
        return DEFAULT_BOOK_COVER_URL
    raw = cover_url.strip()
    if not raw:
        return DEFAULT_BOOK_COVER_URL
    if raw.startswith(("http://", "https://", "/static/")):
        return raw
    if raw.startswith("static/"):
        return f"/{raw}"
    return f"/static/images/books/{raw}"


def day_range_for_db(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Границы суток без tzinfo — для TIMESTAMP WITHOUT TIME ZONE в PostgreSQL."""
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.max.time())
    return start, end
