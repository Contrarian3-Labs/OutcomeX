from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.api.routes.payments import (
    _apply_payment_state,
    _ensure_onchain_order_anchor,
    _backfill_order_chain_anchor_from_receipt,
    _mark_authoritative_paid_projection,
)
from app.api.routes.primary_issuance import (
    _resolve_primary_purchase_for_hsp_event,
    apply_primary_purchase_hsp_webhook,
)
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Order, Payment, utc_now
from app.integrations.onchain_broadcaster import OnchainBroadcaster, get_onchain_broadcaster
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import TransactionSender, get_onchain_transaction_sender

router = APIRouter()

SUCCESS_STATUSES = {"completed", "confirmed", "succeeded", "payment-successful"}
FAILED_STATUSES = {"cancelled", "failed", "rejected", "payment-failed"}
PENDING_STATUSES = {"created", "pending", "processing", "payment-included"}


def _map_hsp_status(status_value: str) -> PaymentState:
    normalized = status_value.lower()
    if normalized in SUCCESS_STATUSES:
        return PaymentState.SUCCEEDED
    if normalized in FAILED_STATUSES:
        return PaymentState.FAILED
    if normalized in PENDING_STATUSES:
        return PaymentState.PENDING
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported HSP status")


def _normalize_hsp_tx_hash(raw_tx_hash: str | None) -> str | None:
    if raw_tx_hash is None:
        return None
    normalized = raw_tx_hash.strip().lower()
    return normalized or None


@router.post("/webhooks")
async def ingest_hsp_webhook(
    request: Request,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> dict[str, object]:
    body = await request.body()
    signature_header = request.headers.get("x-signature")
    legacy_signature = request.headers.get("x-hsp-signature")
    legacy_timestamp = request.headers.get("x-hsp-timestamp")
    if not container.hsp_adapter.verify_webhook_signature(
        body=body,
        signature_header=signature_header,
        legacy_signature=legacy_signature,
        legacy_timestamp=legacy_timestamp,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid HSP signature")

    event = container.hsp_adapter.parse_webhook(body)
    payment = db.scalar(select(Payment).where(Payment.provider_reference == event.payment_request_id))
    if payment is None:
        payment = db.scalar(select(Payment).where(Payment.merchant_order_id == event.cart_mandate_id))
    if payment is None and event.flow_id:
        payment = db.scalar(select(Payment).where(Payment.flow_id == event.flow_id))

    if payment is None:
        primary_purchase = _resolve_primary_purchase_for_hsp_event(event=event, db=db)
        if primary_purchase is not None:
            mapped_state = _map_hsp_status(event.status)
            result = apply_primary_purchase_hsp_webhook(
                purchase=primary_purchase,
                mapped_state=mapped_state,
                event=event,
                container=container,
                onchain_lifecycle=onchain_lifecycle,
                db=db,
            )
            db.commit()
            return result
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
        normalized_tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
        if normalized_tx_hash is None or not normalized_tx_hash.startswith("0x"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Successful HSP webhook must include tx signature",
            )
        reused_tx = db.scalar(
            select(Payment.id).where(
                Payment.id != payment.id,
                Payment.state == PaymentState.SUCCEEDED,
                func.lower(Payment.callback_tx_hash) == normalized_tx_hash,
            )
        )
        if reused_tx is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="HSP tx signature already used by another payment",
            )
        order = db.get(Order, payment.order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        _ensure_onchain_order_anchor(
            order=order,
            container=container,
            order_writer=order_writer,
            onchain_broadcaster=onchain_broadcaster,
            tx_sender=tx_sender,
            db=db,
            unresolved_wallet_detail="Buyer wallet address unresolved for HSP settlement",
        )
        write_result = order_writer.pay_order_by_adapter(order, payment)
        broadcasted_write = tx_sender.send(write_result)
        create_paid_receipt = onchain_broadcaster.broadcast_create_paid_order(write_result=broadcasted_write)
        db.refresh(order)
        db.refresh(payment)
        if order.onchain_order_id is None:
            _backfill_order_chain_anchor_from_receipt(order, create_paid_receipt)
        _mark_authoritative_paid_projection(
            order,
            order_status="PAID",
            event_id=create_paid_receipt.event_id,
        )
        db.add(order)
    _apply_payment_state(payment, state=mapped_state, db=db, order_writer=order_writer, write_chain=False)
    payment.callback_event_id = event.event_id
    payment.callback_state = event.status
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
    db.add(payment)
    db.commit()
    return {
        "payment_id": payment.id,
        "state": payment.state.value,
        "duplicate": False,
    }
