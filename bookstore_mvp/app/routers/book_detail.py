from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Book, Favorite, book_categories
from app.security import get_user_id_from_request

router = APIRouter()


@router.get("/book/{book_id}", name="book_detail")
async def book_detail(request: Request, book_id: int, db=Depends(get_db)):
    result = await db.execute(
        select(Book)
        .options(selectinload(Book.authors), selectinload(Book.categories))
        .where(Book.id == book_id)
    )
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    similar_books: list[Book] = []
    if book.categories:
        cid = book.categories[0].id
        sim = await db.execute(
            select(Book)
            .options(selectinload(Book.authors))
            .join(book_categories, book_categories.c.book_id == Book.id)
            .where(book_categories.c.category_id == cid, Book.id != book_id)
            .order_by(Book.created_at.desc())
            .limit(4)
        )
        similar_books = list(sim.scalars().unique().all())

    uid = get_user_id_from_request(request)
    is_favorite = False
    if uid:
        fav = await db.execute(
            select(Favorite).where(Favorite.user_id == uid, Favorite.book_id == book_id)
        )
        is_favorite = fav.scalar_one_or_none() is not None

    return request.state.templates.TemplateResponse(
        request,
        "book_detail.html",
        {
            "request": request,
            "book": book,
            "similar_books": similar_books,
            "is_favorite": is_favorite,
        },
    )


@router.post("/book/{book_id}/favorite", name="toggle_favorite")
async def toggle_favorite(request: Request, book_id: int, db=Depends(get_db)):
    uid = get_user_id_from_request(request)
    if not uid:
        return RedirectResponse(url=f"/auth?next=/book/{book_id}", status_code=303)

    from app.csrf import verify_csrf

    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    book_result = await db.execute(select(Book.id).where(Book.id == book_id))
    if book_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    existing = await db.execute(
        select(Favorite).where(Favorite.user_id == uid, Favorite.book_id == book_id)
    )
    if existing.scalar_one_or_none() is not None:
        await db.execute(delete(Favorite).where(Favorite.user_id == uid, Favorite.book_id == book_id))
    else:
        db.add(Favorite(user_id=uid, book_id=book_id))

    return RedirectResponse(url=f"/book/{book_id}", status_code=303)
