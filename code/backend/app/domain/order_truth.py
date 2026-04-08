from app.domain.models import Order


AUTHORITATIVE_PAID_ORDER_STATUSES = frozenset(
    {
        "PAID",
        "PREVIEW_READY",
        "CONFIRMED",
        "REJECTED",
        "REFUNDED",
    }
)


def set_authoritative_order_truth(
    order: Order,
    *,
    order_status: str,
    event_id: str,
    cancelled_as_expired: bool | None = None,
) -> None:
    metadata = dict(order.execution_metadata or {})
    metadata["authoritative_order_status"] = order_status
    metadata["authoritative_order_event_id"] = event_id
    metadata["authoritative_paid_projection"] = order_status in AUTHORITATIVE_PAID_ORDER_STATUSES
    if cancelled_as_expired is not None:
        metadata["cancelled_as_expired"] = cancelled_as_expired
    order.execution_metadata = metadata
