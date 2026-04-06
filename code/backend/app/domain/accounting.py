from app.domain.models import Order


def effective_paid_amount_cents(*, order: Order, paid_amount_cents: int) -> int:
    if paid_amount_cents < 0:
        raise ValueError("paid_amount_cents must be non-negative")
    if order.quoted_amount_cents < 0:
        raise ValueError("quoted_amount_cents must be non-negative")
    return min(paid_amount_cents, order.quoted_amount_cents)
