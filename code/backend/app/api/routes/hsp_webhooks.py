from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.api.routes.payments import _apply_payment_state
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Payment, utc_now
from app.onchain.order_writer import OrderWriter, get_order_writer

router = APIRouter()

SUCCESS_STATUSES = {"completed", "confirmed", "succeeded"}
FAILED_STATUSES = {"cancelled", "failed", "rejected"}
PENDING_STATUSES = {"created", "pending", "processing"}


def _map_hsp_status(status_value: str) -> PaymentState:
    normalized = status_value.lower()
    if normalized in SUCCESS_STATUSES:
        return PaymentState.SUCCEEDED
    if normalized in FAILED_STATUSES:
        return PaymentState.FAILED
    if normalized in PENDING_STATUSES:
        return PaymentState.PENDING
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported HSP status")


@router.post("/webhooks")
async def ingest_hsp_webhook(
    request: Request,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> dict[str, object]:
    body = await request.body()
    signature = request.headers.get("x-hsp-signature")
    timestamp = request.headers.get("x-hsp-timestamp")
    if not container.hsp_adapter.verify_webhook_signature(body=body, signature=signature, timestamp=timestamp):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid HSP signature")

    event = container.hsp_adapter.parse_webhook(body)
    payment = db.scalar(select(Payment).where(Payment.flow_id == event.flow_id))
    if payment is None:
        payment = db.scalar(select(Payment).where(Payment.merchant_order_id == event.merchant_order_id))
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payment.callback_event_id == event.event_id:
        return {
            "payment_id": payment.id,
            "state": payment.state.value,
            "duplicate": True,
        }

    payment.callback_event_id = event.event_id
    payment.callback_state = event.status
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = event.tx_hash
    _apply_payment_state(payment, state=_map_hsp_status(event.status), db=db, order_writer=order_writer)
    db.commit()
    return {
        "payment_id": payment.id,
        "state": payment.state.value,
        "duplicate": False,
    }
