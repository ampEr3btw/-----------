from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.cart_cookie import clear_cart_cookie, loads_cart
from app.config import Config
from app.constants import DELIVERY_COURIER, DELIVERY_PICKUP, STATUS_NEW
from app.csrf import ensure_csrf_token, verify_csrf
from app.database import get_db
from app.delivery_stub import quote_delivery
from app.limits import limiter
from app.models import Book, Order, OrderItem, StockMovement, User
from app.order_number import generate_order_number
from app.security import get_user_id_from_request

router = APIRouter()


def _generate_pickup_code() -> str:
    from random import randint

    return f"{randint(0, 999999):06d}"


async def _cart_lines(db, cart: dict[str, int]) -> tuple[list[tuple[Book, int, Decimal]], Decimal]:
    if not cart:
        return [], Decimal("0")
    ids = [int(k) for k in cart.keys()]
    result = await db.execute(select(Book).where(Book.id.in_(ids)))
    by_id = {b.id: b for b in result.scalars().all()}
    lines: list[tuple[Book, int, Decimal]] = []
    goods = Decimal("0")
    for bid_str, qty in cart.items():
        bid = int(bid_str)
        b = by_id.get(bid)
        if not b:
            continue
        q = int(qty)
        price = b.price
        lines.append((b, q, price))
        goods += price * q
    return lines, goods


@router.get("/checkout", name="checkout_page")
async def checkout_page(request: Request, db=Depends(get_db)):
    user_id = get_user_id_from_request(request)
    if not user_id:
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Войдите в аккаунт, чтобы оформить заказ",
                "next": "/checkout",
                "csrf_token": csrf_token,
            },
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    cart = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    lines, goods_total = await _cart_lines(db, cart)

    if not lines:
        return request.state.templates.TemplateResponse(
            request,
            "cart.html",
            {
                "request": request,
                "cart_items": [],
                "total": 0,
                "is_authenticated": True,
                "error": "Корзина пуста",
            },
        )

    return request.state.templates.TemplateResponse(
        request,
        "checkout.html",
        {
            "request": request,
            "user": user,
            "checkout_lines": [
                {"book": b, "quantity": q, "subtotal": float(price * q)} for b, q, price in lines
            ],
            "goods_total": float(goods_total),
        },
    )


@router.post("/order/create", name="create_order")
@limiter.limit("20/minute")
async def create_order(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    user_id = get_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    cart = loads_cart(request.cookies.get(Config.CART_COOKIE_NAME), Config.CART_MAX_AGE_SECONDS)
    if not cart:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    delivery_raw = (form.get("delivery_type") or "").strip()
    if delivery_raw not in (DELIVERY_PICKUP, DELIVERY_COURIER):
        raise HTTPException(status_code=400, detail="Выберите способ получения")

    delivery_address = (form.get("delivery_address") or "").strip() or None
    if delivery_raw == DELIVERY_COURIER and not delivery_address:
        raise HTTPException(status_code=400, detail="Укажите адрес доставки")

    last_name = (form.get("last_name") or "").strip()
    first_name = (form.get("first_name") or "").strip()
    middle_name = (form.get("middle_name") or "").strip() or None
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip().lower()

    if last_name:
        user.last_name = last_name
    if first_name:
        user.first_name = first_name
    user.middle_name = middle_name
    if phone and phone != user.phone:
        taken = await db.execute(select(User.id).where(User.phone == phone, User.id != user.id))
        if taken.scalar_one_or_none() is None:
            user.phone = phone
    if email and email != user.email:
        taken = await db.execute(select(User.id).where(User.email == email, User.id != user.id))
        if taken.scalar_one_or_none() is None:
            user.email = email

    book_ids = sorted({int(k) for k in cart.keys()})
    locked = await db.execute(select(Book).where(Book.id.in_(book_ids)).order_by(Book.id).with_for_update())
    books = {b.id: b for b in locked.scalars().all()}

    if len(books) != len(book_ids):
        raise HTTPException(status_code=400, detail="Некоторые позиции недоступны")

    lines: list[tuple[Book, int, Decimal]] = []
    goods_total = Decimal("0")
    for bid_str, qty in cart.items():
        bid = int(bid_str)
        b = books[bid]
        q = int(qty)
        if b.quantity < q:
            raise HTTPException(
                status_code=400,
                detail=f"Недостаточно остатков для «{b.title}» (доступно {b.quantity})",
            )
        price = b.price
        lines.append((b, q, price))
        goods_total += price * q

    delivery_cost = quote_delivery(delivery_address or "") if delivery_raw == DELIVERY_COURIER else Decimal("0")
    total_amount = goods_total + delivery_cost

    order_number = generate_order_number()
    pickup_code = _generate_pickup_code() if delivery_raw == DELIVERY_PICKUP else None
    new_order = Order(
        order_number=order_number,
        user_id=user_id,
        status=STATUS_NEW,
        delivery_type=delivery_raw,
        pickup_code=pickup_code,
        delivery_address=delivery_address,
        delivery_cost=delivery_cost,
        total_amount=total_amount,
    )
    db.add(new_order)
    await db.flush()

    for b, q, price in lines:
        db.add(
            OrderItem(
                order_id=new_order.id,
                book_id=b.id,
                quantity=q,
                price_at_purchase=price,
            )
        )
        b.quantity -= q
        db.add(
            StockMovement(
                book_id=b.id,
                qty_delta=-int(q),
                reason="order_create",
                source_ref=f"order:{new_order.order_number}",
                admin_id=None,
            )
        )

    await db.commit()
    await db.refresh(new_order)

    response = RedirectResponse(url=f"/thank-you/{new_order.id}", status_code=303)
    clear_cart_cookie(response)
    return response


@router.get("/thank-you/{order_id}", name="thank_you_page")
async def thank_you_page(request: Request, order_id: int, db=Depends(get_db)):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return RedirectResponse(url="/auth?next=/profile", status_code=303)

    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.book))
        .where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    return request.state.templates.TemplateResponse(
        request,
        "thank_you.html",
        {"request": request, "order": order},
    )
