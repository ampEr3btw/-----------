from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Author, Book, Category, book_authors, book_categories

router = APIRouter()


def _parse_decimal_or_none(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    value = raw.strip().replace(",", ".")
    if value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _apply_filters(
    stmt: Select[Any],
    q: str | None,
    category_ids: list[int],
    min_price: Decimal | None,
    max_price: Decimal | None,
    in_stock: bool,
) -> Select[Any]:
    if q:
        term = f"%{q.strip()}%"
        author_match = exists().where(
            and_(
                book_authors.c.book_id == Book.id,
                book_authors.c.author_id == Author.id,
                or_(
                    Author.last_name.ilike(term),
                    Author.first_name.ilike(term),
                    Author.middle_name.ilike(term),
                ),
            )
        )
        stmt = stmt.where(
            or_(
                Book.title.ilike(term),
                Book.isbn.ilike(term),
                author_match,
            )
        )

    if category_ids:
        cat_match = exists().where(
            and_(
                book_categories.c.book_id == Book.id,
                book_categories.c.category_id.in_(category_ids),
            )
        )
        stmt = stmt.where(cat_match)

    if min_price is not None:
        stmt = stmt.where(Book.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Book.price <= max_price)
    if in_stock:
        stmt = stmt.where(Book.quantity > 0)

    return stmt


@router.get("/catalog", name="catalog_page")
async def catalog_page(
    request: Request,
    db=Depends(get_db),
    q: str | None = Query(None),
    category: list[int] = Query(default=[]),
    min_price: str | None = Query(None),
    max_price: str | None = Query(None),
    in_stock: bool = Query(False),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
):
    per_page = 12
    category_ids = [int(c) for c in category if c is not None]
    min_price_val = _parse_decimal_or_none(min_price)
    max_price_val = _parse_decimal_or_none(max_price)
    if (
        min_price_val is not None
        and max_price_val is not None
        and min_price_val > max_price_val
    ):
        min_price_val, max_price_val = max_price_val, min_price_val

    base = select(Book).options(selectinload(Book.authors), selectinload(Book.categories))
    base = _apply_filters(base, q, category_ids, min_price_val, max_price_val, in_stock)

    if sort == "price_asc":
        base = base.order_by(Book.price.asc())
    elif sort == "price_desc":
        base = base.order_by(Book.price.desc())
    elif sort == "title_asc":
        base = base.order_by(Book.title.asc(), Book.id.desc())
    elif sort == "title_desc":
        base = base.order_by(Book.title.desc(), Book.id.desc())
    elif sort == "stock_desc":
        base = base.order_by(Book.quantity.desc(), Book.id.desc())
    elif sort == "stock_asc":
        base = base.order_by(Book.quantity.asc(), Book.id.desc())
    elif sort == "oldest":
        base = base.order_by(Book.created_at.asc())
    else:
        sort = "newest"
        base = base.order_by(Book.created_at.desc())

    count_stmt = select(func.count()).select_from(Book)
    count_stmt = _apply_filters(count_stmt, q, category_ids, min_price_val, max_price_val, in_stock)
    total_books = int((await db.execute(count_stmt)).scalar() or 0)
    total_pages = (total_books + per_page - 1) // per_page if total_books else 0

    offset = (page - 1) * per_page
    result = await db.execute(base.offset(offset).limit(per_page))
    books = result.scalars().unique().all()

    categories_result = await db.execute(select(Category).order_by(Category.name))
    categories = categories_result.scalars().all()

    return request.state.templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "request": request,
            "books": books,
            "categories": categories,
            "selected_categories": category_ids,
            "current_sort": sort,
            "current_page": page,
            "total_pages": total_pages,
            "total_books": total_books,
            "search_query": q,
            "in_stock_enabled": in_stock,
        },
    )
