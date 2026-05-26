from __future__ import annotations

import hashlib

import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request, Response
from jose import JWTError, jwt

from app.config import Config


def _password_for_bcrypt(plain_password: str) -> bytes:
    """SHA-256 (hex) как вход bcrypt — как раньше с passlib, без лимита 72 байта на пароль."""
    return hashlib.sha256(plain_password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_for_bcrypt(password), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        h = hashed_password.encode("utf-8")
        if bcrypt.checkpw(_password_for_bcrypt(plain_password), h):
            return True
        if len(plain_password.encode("utf-8")) <= 72:
            return bcrypt.checkpw(plain_password.encode("utf-8"), h)
    except (ValueError, TypeError):
        pass
    return False


def create_user_token(user_id: int, expires_minutes: int | None = None) -> str:
    minutes = expires_minutes or Config.ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode(
        {"sub": str(user_id), "typ": "user", "exp": expire},
        Config.SECRET_KEY,
        algorithm=Config.ALGORITHM,
    )


def create_admin_token(admin_id: int, role_name: str, expires_minutes: int | None = None) -> str:
    minutes = expires_minutes or Config.ADMIN_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode(
        {"sub": str(admin_id), "typ": "admin", "role": role_name, "exp": expire},
        Config.SECRET_KEY,
        algorithm=Config.ALGORITHM,
    )


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, Config.SECRET_KEY, algorithms=[Config.ALGORITHM])
    except JWTError:
        return None


def cookie_secure(request: Request) -> bool:
    if Config.DEBUG:
        return False
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded and forwarded.lower() == "https":
        return True
    return request.url.scheme == "https"


def set_user_auth_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        max_age=Config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def set_admin_auth_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key="admin_access_token",
        value=token,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        max_age=Config.ADMIN_TOKEN_EXPIRE_MINUTES * 60,
        path="/admin",
    )


def clear_user_auth_cookie(response: Response) -> None:
    response.delete_cookie("access_token", path="/")


def clear_admin_auth_cookie(response: Response) -> None:
    response.delete_cookie("admin_access_token", path="/admin")


def get_user_id_from_request(request: Request) -> int | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != "user":
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


def get_admin_context_from_request(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get("admin_access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != "admin":
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        admin_id = int(sub)
    except (TypeError, ValueError):
        return None
    role = payload.get("role")
    if not isinstance(role, str):
        return None
    return {"admin_id": admin_id, "role": role}
