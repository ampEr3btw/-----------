from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.constants import CLIENT_CANCELLABLE_STATUSES, STATUS_CANCELLED
from app.csrf import ensure_csrf_token, verify_csrf
from app.database import get_db
from app.models import Book, Favorite, Order, OrderItem, StockMovement, User
from app.security import get_user_id_from_request

router = APIRouter()


@router.get("/profile", name="profile_page")
async def profile_page(request: Request, db=Depends(get_db)):
    user_id = get_user_id_from_request(request)
    if not user_id:
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Пожалуйста, войдите в аккаунт",
                "next": "/profile",
                "csrf_token": csrf_token,
            },
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/logout", status_code=303)

    orders_result = await db.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(desc(Order.created_at))
        .limit(50)
    )
    orders = orders_result.scalars().all()

    fav_result = await db.execute(
        select(Book)
        .options(selectinload(Book.authors))
        .join(Favorite, Favorite.book_id == Book.id)
        .where(Favorite.user_id == user_id)
        .order_by(desc(Favorite.created_at))
    )
    favorites = fav_result.scalars().unique().all()

    success = None
    if request.query_params.get("saved") == "1":
        success = "Профиль сохранён"

    return request.state.templates.TemplateResponse(
        request,
        "profile.html",
        {
            "request": request,
            "user": user,
            "orders": orders,
            "favorites": favorites,
            "success": success,
        },
    )


@router.get("/orders/{order_id}", name="order_detail_page")
async def order_detail_page(order_id: int, request: Request, db=Depends(get_db)):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return RedirectResponse(url=f"/auth?next=/orders/{order_id}", status_code=303)

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
        "order_detail.html",
        {
            "request": request,
            "order": order,
            "can_cancel": order.status in CLIENT_CANCELLABLE_STATUSES,
        },
    )


@router.post("/orders/{order_id}/cancel", name="order_cancel")
async def order_cancel(order_id: int, request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    user_id = get_user_id_from_request(request)
    if not user_id:
        return RedirectResponse(url=f"/auth?next=/orders/{order_id}", status_code=303)

    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.book))
        .where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    if order.status not in CLIENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Этот заказ уже нельзя отменить")

    for item in order.items:
        if item.book:
            item.book.quantity += int(item.quantity)
            db.add(
                StockMovement(
                    book_id=item.book.id,
                    qty_delta=int(item.quantity),
                    reason="order_cancel_client",
                    source_ref=f"order:{order.order_number}",
                    admin_id=None,
                )
            )

    order.status = STATUS_CANCELLED
    order.admin_comment = order.admin_comment or "Отменён клиентом"
    order.updated_at = datetime.now()
    await db.commit()

    return RedirectResponse(url=f"/orders/{order_id}", status_code=303)


@router.post("/profile/update", name="profile_update")
async def profile_update(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    user_id = get_user_id_from_request(request)
    if not user_id:
        return RedirectResponse(url="/auth?next=/profile", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/logout", status_code=303)

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

    return RedirectResponse(url="/profile?saved=1", status_code=303)
