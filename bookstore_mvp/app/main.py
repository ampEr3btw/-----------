from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.config import Config
from app.constants import STATUS_LABELS
from app.csrf import ensure_csrf_token
from app.database import engine
from app.helpers import (
    author_display_name,
    book_authors_line,
    book_categories_line,
    book_cover_src,
    user_display_name,
)
from app.limits import limiter
from app.routers import admin, auth, book_detail, cart, catalog, checkout, main as main_router, profile


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title=Config.APP_NAME, debug=Config.DEBUG, lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="app/templates")


def catalog_url(request: Request, **overrides: Any) -> str:
    from urllib.parse import urlencode

    merged: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        merged.setdefault(k, []).append(v)
    for key, value in overrides.items():
        sk = str(key)
        if value is None or value == "":
            merged.pop(sk, None)
        elif isinstance(value, (list, tuple)):
            merged[sk] = [str(x) for x in value]
        else:
            merged[sk] = [str(value)]
    flat: list[tuple[str, str]] = []
    for k, vals in merged.items():
        for v in vals:
            flat.append((k, v))
    qs = urlencode(flat)
    base = str(request.url_for("catalog_page"))
    return f"{base}?{qs}" if qs else base


templates.env.globals["catalog_url"] = catalog_url
templates.env.globals["author_display_name"] = author_display_name
templates.env.globals["book_authors_line"] = book_authors_line
templates.env.globals["book_categories_line"] = book_categories_line
templates.env.globals["book_cover_src"] = book_cover_src
templates.env.globals["user_display_name"] = user_display_name
templates.env.globals["status_labels"] = STATUS_LABELS

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def attach_request_state(request: Request, call_next):
    request.state.templates = templates
    request.state.csrf_token = ensure_csrf_token(request)
    return await call_next(request)


app.include_router(main_router.router)
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(book_detail.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(profile.router)
app.include_router(admin.router, prefix="/admin")

app.add_middleware(
    SessionMiddleware,
    secret_key=Config.SECRET_KEY,
    same_site="lax",
    https_only=not Config.DEBUG,
)
