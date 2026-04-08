from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.api.routes.payments import (
    _apply_payment_state,
    _backfill_order_chain_anchor_from_receipt,
    _mark_authoritative_paid_projection,
)
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Order, Payment, utc_now
from app.integrations.onchain_broadcaster import OnchainBroadcaster, get_onchain_broadcaster
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import TransactionSender, get_onchain_transaction_sender

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
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
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

    if event.amount_cents != payment.amount_cents:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP webhook amount mismatch")
    if event.currency != payment.currency.upper():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP webhook currency mismatch")

    mapped_state = _map_hsp_status(event.status)
    if mapped_state == PaymentState.SUCCEEDED:
        order = db.get(Order, payment.order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        if order.onchain_order_id is None:
            buyer_wallet_address = container.buyer_address_resolver.resolve_wallet(order.user_id)
            if buyer_wallet_address is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Buyer wallet address unresolved for HSP settlement",
                )
            write_result = order_writer.create_order_and_mark_paid(
                order,
                payment,
                buyer_wallet_address=buyer_wallet_address,
            )
            broadcasted_write = tx_sender.send(write_result)
            create_paid_receipt = onchain_broadcaster.broadcast_create_paid_order(write_result=broadcasted_write)
            _backfill_order_chain_anchor_from_receipt(order, create_paid_receipt)
            _mark_authoritative_paid_projection(
                order,
                order_status="PAID",
                event_id=create_paid_receipt.event_id,
            )
        else:
            _mark_authoritative_paid_projection(
                order,
                order_status="PAID",
                event_id=event.event_id,
            )
        db.add(order)
    _apply_payment_state(payment, state=mapped_state, db=db, order_writer=order_writer, write_chain=False)
    payment.callback_event_id = event.event_id
    payment.callback_state = event.status
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = event.tx_hash
    db.add(payment)
    db.commit()
    return {
        "payment_id": payment.id,
        "state": payment.state.value,
        "duplicate": False,
    }
