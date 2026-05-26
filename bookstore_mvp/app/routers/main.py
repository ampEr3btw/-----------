from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Book

router = APIRouter()


@router.get("/", name="main_page")
async def main_page(request: Request, db=Depends(get_db)):
    result = await db.execute(
        select(Book)
        .options(selectinload(Book.authors), selectinload(Book.categories))
        .order_by(desc(Book.created_at))
        .limit(8)
    )
    books = result.scalars().all()
    return request.state.templates.TemplateResponse(
        request,
        "main_page.html",
        {"request": request, "books": books},
    )
