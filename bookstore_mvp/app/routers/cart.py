from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.cart_cookie import clear_cart_cookie, loads_cart, set_cart_cookie
from app.config import Config
from app.csrf import verify_csrf
from app.database import get_db
from app.models import Book
from app.security import get_user_id_from_request

router = APIRouter()


@router.get("/cart", name="cart_page")
async def cart_page(request: Request, db=Depends(get_db)):
    cart_items_raw = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    books_out: list[dict] = []
    total = 0.0

    if cart_items_raw:
        ids = [int(k) for k in cart_items_raw.keys()]
        result = await db.execute(
            select(Book).options(selectinload(Book.authors)).where(Book.id.in_(ids))
        )
        by_id = {b.id: b for b in result.scalars().all()}
        for bid_str, qty in cart_items_raw.items():
            book = by_id.get(int(bid_str))
            if not book:
                continue
            q = int(qty)
            sub = float(book.price) * q
            total += sub
            books_out.append({"book": book, "quantity": q, "subtotal": sub})

    return request.state.templates.TemplateResponse(
        request,
        "cart.html",
        {
            "request": request,
            "cart_items": books_out,
            "total": total,
            "is_authenticated": get_user_id_from_request(request) is not None,
        },
    )


@router.post("/cart/add/{book_id}", name="add_to_cart")
async def add_to_cart(request: Request, book_id: int):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    cart_items = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    key = str(book_id)
    cart_items[key] = cart_items.get(key, 0) + 1
    cart_items[key] = max(1, min(999, int(cart_items[key])))

    response = RedirectResponse(url=request.headers.get("referer") or "/catalog", status_code=303)
    set_cart_cookie(response, request, cart_items, Config.CART_MAX_AGE_SECONDS)
    return response


@router.post("/cart/update", name="update_cart")
async def update_cart(request: Request):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    cart_items = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    for key, value in form.items():
        if not key.startswith("quantity_"):
            continue
        book_id = key.removeprefix("quantity_")
        if book_id not in cart_items:
            continue
        try:
            qty = int(value)
        except (TypeError, ValueError):
            continue
        cart_items[book_id] = max(1, min(999, qty))

    response = RedirectResponse(url="/cart", status_code=303)
    set_cart_cookie(response, request, cart_items, Config.CART_MAX_AGE_SECONDS)
    return response


@router.post("/cart/remove/{book_id}", name="remove_from_cart")
async def remove_from_cart(request: Request, book_id: int):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    cart_items = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    cart_items.pop(str(book_id), None)
    response = RedirectResponse(url="/cart", status_code=303)
    set_cart_cookie(response, request, cart_items, Config.CART_MAX_AGE_SECONDS)
    return response
