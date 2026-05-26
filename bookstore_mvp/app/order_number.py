import secrets
from datetime import datetime, timezone


def generate_order_number() -> str:
    """ORD-ГОД-СЛУЧАЙНЫЙ_СУФФИКС (устойчиво к гонкам; для строго последовательного NNN используйте SQL-сиквенс)."""
    year = datetime.now(timezone.utc).year
    return f"ORD-{year}-{secrets.token_hex(4).upper()}"
