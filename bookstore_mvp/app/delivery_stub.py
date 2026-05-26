from decimal import Decimal

from app.config import Config


def quote_delivery(address: str) -> Decimal:
    """Заглушка расчёта доставки: база + надбавка за длину адреса, с потолком."""
    text = (address or "").strip()
    base = Decimal(str(Config.DELIVERY_STUB_BASE))
    if not text:
        return base.quantize(Decimal("0.01"))
    per = Decimal(str(Config.DELIVERY_STUB_PER_CHAR))
    cap = Decimal(str(Config.DELIVERY_STUB_MAX))
    total = base + per * len(text)
    if total > cap:
        total = cap
    return total.quantize(Decimal("0.01"))
