from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select

from app.limits import limiter
from app.csrf import ensure_csrf_token, verify_csrf
from app.database import get_db
from app.models import User
from app.security import (
    clear_user_auth_cookie,
    create_user_token,
    hash_password,
    set_user_auth_cookie,
    verify_password,
)

router = APIRouter()


def _safe_redirect_target(raw: str | None) -> str:
    if not raw:
        return "/profile"
    url = raw.strip()
    if url.startswith("/") and not url.startswith("//"):
        return url
    return "/profile"


@router.get("/auth", name="auth_page")
async def auth_page(request: Request):
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "auth.html",
        {
            "request": request,
            "next": request.query_params.get("next") or "",
            "csrf_token": csrf_token,
        },
    )


@router.post("/register", name="register")
@limiter.limit("10/minute")
async def register(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    last_name = (form.get("last_name") or "").strip()
    first_name = (form.get("first_name") or "").strip()
    middle_name = (form.get("middle_name") or "").strip() or None
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password")
    confirm = form.get("confirm_password")
    next_url = form.get("next") or ""

    if not all([last_name, first_name, phone, email, password]):
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Заполните обязательные поля",
                "next": next_url,
                "csrf_token": csrf_token,
            },
        )

    if password != confirm:
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Пароли не совпадают",
                "next": next_url,
                "csrf_token": csrf_token,
            },
        )

    exists = await db.execute(select(User.id).where(or_(User.phone == phone, User.email == email)))
    if exists.scalar_one_or_none() is not None:
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Пользователь с таким телефоном или email уже существует",
                "next": next_url,
                "csrf_token": csrf_token,
            },
        )

    user = User(
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        phone=phone,
        email=email,
        password_hash=hash_password(str(password)),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = create_user_token(user.id)
    response = RedirectResponse(url=_safe_redirect_target(next_url), status_code=303)
    set_user_auth_cookie(response, request, token)
    return response


@router.post("/login", name="login")
@limiter.limit("15/minute")
async def login(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    email = (form.get("email") or "").strip().lower()
    password = form.get("password")
    next_url = form.get("next") or ""

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(str(password), user.password_hash):
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "error": "Неверный email или пароль",
                "next": next_url,
                "csrf_token": csrf_token,
            },
        )

    token = create_user_token(user.id)
    response = RedirectResponse(url=_safe_redirect_target(next_url), status_code=303)
    set_user_auth_cookie(response, request, token)
    return response


@router.get("/logout", name="logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    clear_user_auth_cookie(response)
    return response
