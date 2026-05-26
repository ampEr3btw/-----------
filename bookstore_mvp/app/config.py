import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_DB = "postgresql+asyncpg://postgres:postgres@localhost:5432/bookstore_mvp"


@lru_cache
def get_database_url() -> str:
    return os.getenv("DATABASE_URL", _DEFAULT_DB)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-use-long-random-string")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    ADMIN_TOKEN_EXPIRE_MINUTES = int(os.getenv("ADMIN_TOKEN_EXPIRE_MINUTES", "480"))

    APP_NAME = os.getenv("APP_NAME", "Книжный магазин")
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"

    CART_COOKIE_NAME = "bookstore_cart"
    CART_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

    # Заглушка доставки: базовая сумма + за символ адреса (демо)
    DELIVERY_STUB_BASE = float(os.getenv("DELIVERY_STUB_BASE", "299"))
    DELIVERY_STUB_PER_CHAR = float(os.getenv("DELIVERY_STUB_PER_CHAR", "0.15"))
    DELIVERY_STUB_MAX = float(os.getenv("DELIVERY_STUB_MAX", "890"))
