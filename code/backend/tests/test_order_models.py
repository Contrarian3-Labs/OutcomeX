from datetime import datetime, timedelta, timezone

from app.domain.enums import PaymentState
from app.domain.models import Order, Payment


def _order(*, created_at: datetime | None = None, execution_metadata: dict | None = None) -> Order:
    created_at = created_at or datetime.now(timezone.utc)
    return Order(
        id="order-1",
        user_id="user-1",
        machine_id="machine-1",
        chat_session_id="chat-1",
        user_prompt="build",
        recommended_plan_summary="plan",
        quoted_amount_cents=100,
        created_at=created_at,
        execution_metadata=execution_metadata,
    )


def test_payment_state_stays_succeeded_after_later_failed_duplicate_payment() -> None:
    created_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    order = _order(created_at=created_at)
    order.payments = [
        Payment(
            id="pay-1",
            order_id=order.id,
            provider="hsp",
            amount_cents=100,
            currency="USD",
            state=PaymentState.SUCCEEDED,
            created_at=created_at + timedelta(minutes=1),
        ),
        Payment(
            id="pay-2",
            order_id=order.id,
            provider="hsp",
            amount_cents=100,
            currency="USD",
            state=PaymentState.FAILED,
            created_at=created_at + timedelta(minutes=2),
        ),
    ]

    assert order.payment_state == PaymentState.SUCCEEDED
    assert order.unpaid_expiry_at is None
    assert order.is_expired is False


def test_unpaid_expiry_helpers_respect_authoritative_paid_projection() -> None:
    created_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    order = _order(
        created_at=created_at,
        execution_metadata={"authoritative_paid_projection": True},
    )

    assert order.unpaid_expiry_at is None
    assert order.is_expired is False
