import secrets

from fastapi import HTTPException, Request


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return str(token)


def verify_csrf(request: Request, form_token: str | None) -> None:
    expected = request.session.get("csrf")
    if not form_token or not expected or form_token != expected:
        raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")
