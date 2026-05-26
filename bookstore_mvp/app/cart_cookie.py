from __future__ import annotations

import json
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Config

_serializer: URLSafeTimedSerializer | None = None


def _get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(Config.SECRET_KEY, salt="bookstore-cart-v1")
    return _serializer


def dumps_cart(items: dict[str, int]) -> str:
    """items: book_id str -> quantity int"""
    return _get_serializer().dumps(json.dumps(items))


def loads_cart(raw: str | None, max_age: int) -> dict[str, int]:
    if not raw:
        return {}
    try:
        data = _get_serializer().loads(raw, max_age=max_age)
        parsed: dict[str, Any] = json.loads(data)
        out: dict[str, int] = {}
        for k, v in parsed.items():
            try:
                out[str(int(k))] = max(1, min(999, int(v)))
            except (TypeError, ValueError):
                continue
        return out
    except (BadSignature, SignatureExpired, json.JSONDecodeError, TypeError):
        return {}


def set_cart_cookie(response, request, items: dict[str, int], max_age: int) -> None:
    from app.security import cookie_secure

    value = dumps_cart(items)
    response.set_cookie(
        key=Config.CART_COOKIE_NAME,
        value=value,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def clear_cart_cookie(response) -> None:
    response.delete_cookie(Config.CART_COOKIE_NAME, path="/")
