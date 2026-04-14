import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState
from app.integrations.hsp_adapter import HSPWebhookEvent
from app.domain.models import Machine, Order, Payment, utc_now
from app.domain.order_truth import set_authoritative_order_truth
from app.domain.rules import has_sufficient_payment, is_dividend_eligible
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt
from app.integrations.onchain_broadcaster import OnchainBroadcaster, get_onchain_broadcaster
from app.integrations.onchain_payment_verifier import OnchainPaymentVerifier, get_onchain_payment_verifier
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.receipts import get_receipt_reader
from app.onchain.tx_sender import TransactionSender, get_onchain_transaction_sender
from app.onchain.tx_sender import encode_contract_call
from app.runtime.cost_service import RuntimeCostService, get_runtime_cost_service
from app.schemas.payment import (
    DirectPaymentFinalizeRequest,
    DirectPaymentFinalizeResponse,
    DirectPaymentIntentRequest,
    DirectPaymentIntentResponse,
    DirectPaymentSyncRequest,
    DirectPaymentSyncResponse,
    HSPPaymentSyncResponse,
    MockPaymentConfirmRequest,
    MockPaymentConfirmResponse,
    PaymentIntentRequest,
    PaymentIntentResponse,
)

router = APIRouter()
PWR_WEI_MULTIPLIER = Decimal("1000000000000000000")


TERMINAL_PAYMENT_STATES = {PaymentState.SUCCEEDED, PaymentState.FAILED, PaymentState.REFUNDED}
HSP_STABLECOIN_CURRENCIES = {"USDC", "USDT"}
SUCCESS_STATUSES = {"completed", "confirmed", "succeeded", "payment-successful"}
FAILED_STATUSES = {"cancelled", "failed", "rejected", "payment-failed"}
PENDING_STATUSES = {"created", "pending", "processing", "payment-included", "payment-required"}
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


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


def _stablecoin_smallest_units_from_cents(amount_cents: int) -> int:
    return amount_cents * 10_000


def _hsp_receipt_confirms_payment(*, payment: Payment, event: HSPWebhookEvent, container: Container) -> bool:
    tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
    if tx_hash is None or not tx_hash.startswith("0x"):
        return False

    receipt = get_receipt_reader().get_receipt(tx_hash)
    if receipt is None or receipt.status != 1:
        return False

    token_address = container.contracts_registry.payment_token(payment.currency.upper()).lower()
    expected_amount = _stablecoin_smallest_units_from_cents(payment.amount_cents)

    for raw_log in receipt.metadata.get("logs", []):
        topics = [str(topic).lower() for topic in raw_log.get("topics", [])]
        if not topics or topics[0] != ERC20_TRANSFER_TOPIC:
            continue
        if str(raw_log.get("address", "")).lower() != token_address:
            continue
        try:
            amount = int(str(raw_log.get("data", "0x0")), 16)
        except ValueError:
            continue
        if amount == expected_amount:
            return True
    return False


def _effective_hsp_mapped_state(*, payment: Payment, event: HSPWebhookEvent, container: Container) -> PaymentState:
    mapped_state = _map_hsp_status(event.status)
    if mapped_state == PaymentState.PENDING and event.status.lower() == "payment-included":
        if _hsp_receipt_confirms_payment(payment=payment, event=event, container=container):
            return PaymentState.SUCCEEDED
    return mapped_state


def _query_hsp_payment_event(payment: Payment, *, container: Container) -> HSPWebhookEvent | None:
    if payment.provider != "hsp" or not container.hsp_adapter.is_live_configured:
        return None
    if payment.provider_reference:
        return container.hsp_adapter.query_payment_status(
            payment_request_id=payment.provider_reference,
            fallback_amount_cents=payment.amount_cents,
            fallback_currency=payment.currency,
        )
    if payment.flow_id:
        return container.hsp_adapter.query_payment_status(
            flow_id=payment.flow_id,
            fallback_amount_cents=payment.amount_cents,
            fallback_currency=payment.currency,
        )
    if payment.merchant_order_id:
        return container.hsp_adapter.query_payment_status(
            cart_mandate_id=payment.merchant_order_id,
            fallback_amount_cents=payment.amount_cents,
            fallback_currency=payment.currency,
        )
    return None


def _apply_hsp_payment_event(
    payment: Payment,
    *,
    event: HSPWebhookEvent,
    db: Session,
    container: Container,
    order_writer: OrderWriter,
    onchain_broadcaster: OnchainBroadcaster,
    tx_sender: TransactionSender,
) -> PaymentState:
    if event.amount_cents is not None and event.amount_cents != payment.amount_cents:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP payment amount mismatch")
    if event.currency is not None and event.currency != payment.currency.upper():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP payment currency mismatch")

    mapped_state = _effective_hsp_mapped_state(payment=payment, event=event, container=container)
    if mapped_state == PaymentState.SUCCEEDED and payment.state != PaymentState.SUCCEEDED:
        normalized_tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
        if normalized_tx_hash is None or not normalized_tx_hash.startswith("0x"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Successful HSP payment must include tx signature",
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
    return mapped_state


def sync_hsp_payment(
    payment: Payment,
    *,
    db: Session,
    container: Container,
    order_writer: OrderWriter,
    onchain_broadcaster: OnchainBroadcaster,
    tx_sender: TransactionSender,
) -> tuple[bool, str | None]:
    event = _query_hsp_payment_event(payment, container=container)
    if event is None:
        return False, None
    _apply_hsp_payment_event(
        payment,
        event=event,
        db=db,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
    )
    db.commit()
    return True, event.status


def sync_pending_hsp_payments_once(*, session_factory, container: Container, limit: int = 50) -> int:
    if not container.hsp_adapter.is_live_configured:
        return 0
    synced = 0
    order_writer = get_order_writer()
    onchain_broadcaster = get_onchain_broadcaster()
    tx_sender = get_onchain_transaction_sender()
    with session_factory() as db:
        payments = list(
            db.scalars(
                select(Payment)
                .where(
                    Payment.provider == "hsp",
                    Payment.state == PaymentState.PENDING,
                )
                .order_by(Payment.created_at.asc())
                .limit(limit)
            )
        )
        for payment in payments:
            try:
                polled, _ = sync_hsp_payment(
                    payment,
                    db=db,
                    container=container,
                    order_writer=order_writer,
                    onchain_broadcaster=onchain_broadcaster,
                    tx_sender=tx_sender,
                )
            except Exception:
                db.rollback()
                continue
            if polled:
                synced += 1
    return synced


def _ensure_demo_write_allowed(*, detail: str) -> None:
    if get_settings().env not in {"dev", "test"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def _existing_active_hsp_payment(order_id: str, db: Session) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.order_id == order_id,
            Payment.provider == "hsp",
            Payment.state.in_((PaymentState.PENDING, PaymentState.SUCCEEDED)),
        )
    )


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _freeze_settlement_policy_if_fully_paid(order: Order, db: Session) -> bool:
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    if not has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents):
        return False

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if order.settlement_beneficiary_user_id is None:
        dividend_eligible = is_dividend_eligible(order.user_id, machine.owner_user_id)
        order.settlement_beneficiary_user_id = machine.owner_user_id
        order.settlement_is_self_use = not dividend_eligible
        order.settlement_is_dividend_eligible = dividend_eligible
    machine.has_active_tasks = True
    db.add(order)
    db.add(machine)
    return True


def _pwr_quote_to_wei_string(pwr_quote: str) -> str:
    return str(int((Decimal(pwr_quote) * PWR_WEI_MULTIPLIER).to_integral_value()))


def _pwr_quote_to_wei_int(pwr_quote: str) -> int:
    return int(_pwr_quote_to_wei_string(pwr_quote))


def _normalize_signature(signature: str) -> str:
    normalized = str(signature).strip().lower()
    if not normalized.startswith("0x"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature must be 0x-prefixed")
    if len(normalized) != 132:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature must be 65 bytes")
    return normalized


def _split_signature(signature: str) -> tuple[int, str, str]:
    normalized = _normalize_signature(signature)[2:]
    r = "0x" + normalized[:64]
    s = "0x" + normalized[64:128]
    v = int(normalized[128:130], 16)
    if v in {0, 1}:
        v += 27
    return v, r, s


def _build_direct_signing_request(
    *,
    currency: str,
    buyer_wallet_address: str | None,
    amount_cents: int,
    token_address: str,
    router_address: str,
    settlement_escrow: str,
    chain_id: int,
    permit2_address: str,
) -> dict[str, Any] | None:
    if currency in {"PWR", "USDT"}:
        return None
    if currency == "USDC":
        settings = get_settings()
        if not buyer_wallet_address:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="wallet_address is required for USDC direct payment intent",
            )
        valid_after = int((utc_now() - timedelta(minutes=1)).timestamp())
        valid_before = int((utc_now() + timedelta(minutes=30)).timestamp())
        nonce = "0x" + secrets.token_hex(32)
        return {
            "kind": "eip712",
            "primaryType": "ReceiveWithAuthorization",
            "domain": {
                "name": settings.onchain_usdc_eip3009_name,
                "version": settings.onchain_usdc_eip3009_version,
                "chainId": chain_id,
                "verifyingContract": token_address,
            },
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "ReceiveWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "message": {
                "from": buyer_wallet_address.lower(),
                "to": settlement_escrow,
                "value": str(amount_cents),
                "validAfter": str(valid_after),
                "validBefore": str(valid_before),
                "nonce": nonce,
            },
        }
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")


def _direct_wallet_submit_payload(direct_intent) -> tuple[str, dict[str, Any]]:
    calldata = encode_contract_call(direct_intent)
    if calldata is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unsupported direct intent method: {direct_intent.method_name}",
        )
    wallet_submit_payload = {
        "to": direct_intent.contract_address,
        "data": calldata,
        "value": "0x0",
        **direct_intent.payload,
    }
    return calldata, wallet_submit_payload


def _apply_payment_state(
    payment: Payment,
    *,
    state: PaymentState,
    db: Session,
    order_writer: OrderWriter,
    write_chain: bool = True,
) -> Order:
    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if payment.state in TERMINAL_PAYMENT_STATES and payment.state != state:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is already in terminal state")
    if payment.state == state:
        return order

    payment.state = state
    db.add(payment)
    db.flush()
    if state == PaymentState.SUCCEEDED and _freeze_settlement_policy_if_fully_paid(order, db) and write_chain:
        order_writer.mark_order_paid(order, payment)
    return order


def _persist_order_chain_anchor(
    order: Order,
    *,
    onchain_order_id: str,
    create_order_tx_hash: str,
    create_order_event_id: str,
    create_order_block_number: int,
) -> None:
    conflict_fields = (
        ("onchain_order_id", order.onchain_order_id, onchain_order_id),
        ("create_order_tx_hash", order.create_order_tx_hash, create_order_tx_hash),
        ("create_order_event_id", order.create_order_event_id, create_order_event_id),
        ("create_order_block_number", order.create_order_block_number, create_order_block_number),
    )
    for field_name, existing_value, new_value in conflict_fields:
        if existing_value is not None and existing_value != new_value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Order already anchored with conflicting {field_name}",
            )

    order.onchain_order_id = onchain_order_id
    order.create_order_tx_hash = create_order_tx_hash
    order.create_order_event_id = create_order_event_id
    order.create_order_block_number = create_order_block_number


def _persist_order_tx_correlation(order: Order, *, payment_tx_hash: str) -> None:
    metadata = dict(order.execution_metadata or {})
    existing = metadata.get("last_payment_tx_hash")
    if existing is not None and existing != payment_tx_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order already correlated with a different payment tx hash",
        )
    metadata["last_payment_tx_hash"] = payment_tx_hash
    order.execution_metadata = metadata


def _backfill_order_chain_anchor_from_verification(order: Order, verification) -> None:
    if verification.state != PaymentState.SUCCEEDED:
        return
    if (
        verification.evidence_order_id is None
        or verification.evidence_create_order_tx_hash is None
        or verification.evidence_create_order_event_id is None
        or verification.evidence_create_order_block_number is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onchain evidence missing create+paid anchor fields",
        )
    _persist_order_chain_anchor(
        order,
        onchain_order_id=verification.evidence_order_id,
        create_order_tx_hash=verification.evidence_create_order_tx_hash,
        create_order_event_id=verification.evidence_create_order_event_id,
        create_order_block_number=verification.evidence_create_order_block_number,
    )


def _backfill_order_chain_anchor_from_receipt(order: Order, receipt: OnchainCreateOrderReceipt) -> None:
    _persist_order_chain_anchor(
        order,
        onchain_order_id=receipt.onchain_order_id,
        create_order_tx_hash=receipt.tx_hash,
        create_order_event_id=receipt.event_id,
        create_order_block_number=receipt.block_number,
    )


def _mark_authoritative_paid_projection(
    order: Order,
    *,
    order_status: str,
    event_id: str,
) -> None:
    set_authoritative_order_truth(order, order_status=order_status, event_id=event_id)


def _mark_authoritative_order_created_projection(order: Order, *, event_id: str) -> None:
    set_authoritative_order_truth(order, order_status="CREATED", event_id=event_id)


def _resolve_buyer_wallet_or_409(*, container: Container, order: Order, detail: str) -> str:
    buyer_wallet_address = container.buyer_address_resolver.resolve_wallet(order.user_id)
    if buyer_wallet_address is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return buyer_wallet_address.lower()


def _ensure_onchain_order_anchor(
    *,
    order: Order,
    container: Container,
    order_writer: OrderWriter,
    onchain_broadcaster: OnchainBroadcaster,
    tx_sender: TransactionSender,
    db: Session,
    unresolved_wallet_detail: str,
    gross_amount_override: int | None = None,
) -> None:
    if order.onchain_order_id is not None:
        return
    buyer_wallet_address = _resolve_buyer_wallet_or_409(
        container=container,
        order=order,
        detail=unresolved_wallet_detail,
    )
    write_result = order_writer.create_order(
        order,
        buyer_wallet_address=buyer_wallet_address,
        gross_amount_override=gross_amount_override,
    )
    broadcasted_write = tx_sender.send(write_result)
    create_order_receipt = onchain_broadcaster.broadcast_create_order(write_result=broadcasted_write)
    db.refresh(order)
    _backfill_order_chain_anchor_from_receipt(order, create_order_receipt)
    _mark_authoritative_order_created_projection(order, event_id=create_order_receipt.event_id)
    db.add(order)


@router.post(
    "/orders/{order_id}/intent",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create HSP stablecoin checkout",
    description="Formal stablecoin checkout path for OutcomeX. Available stablecoins depend on the deployed HSP app configuration.",
)
def create_payment_intent(
    order_id: str,
    payload: PaymentIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
) -> PaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payload.currency.upper()
    if currency not in HSP_STABLECOIN_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HSP checkout only supports USDC or USDT stablecoins",
        )
    if not container.hsp_adapter.supports_currency(currency):
        enabled = ", ".join(container.hsp_adapter.supported_currencies) or "none"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"HSP checkout for {currency} is not enabled on this deployment (enabled: {enabled})",
        )

    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HSP payment amount must match quoted order amount",
        )
    if order.is_cancelled or order.is_expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not payable")

    _ensure_onchain_order_anchor(
        order=order,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
        db=db,
        unresolved_wallet_detail="Buyer wallet address unresolved for HSP settlement",
    )

    existing_payment = _existing_active_hsp_payment(order.id, db)
    if existing_payment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active HSP payment already exists for this order",
        )

    try:
        merchant_order = container.hsp_adapter.create_payment_intent(
            order_id=order.id,
            amount_cents=payload.amount_cents,
            currency=currency,
            expires_at=order.unpaid_expiry_at,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    payment = Payment(
        order_id=order.id,
        provider=merchant_order.provider,
        provider_reference=merchant_order.provider_reference,
        merchant_order_id=merchant_order.merchant_order_id,
        flow_id=merchant_order.flow_id,
        checkout_url=merchant_order.payment_url,
        provider_payload=merchant_order.provider_payload,
        amount_cents=payload.amount_cents,
        currency=currency,
        state=PaymentState.PENDING,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return PaymentIntentResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider=payment.provider,
        provider_reference=merchant_order.provider_reference,
        checkout_url=merchant_order.payment_url,
        flow_id=merchant_order.flow_id,
        merchant_order_id=merchant_order.merchant_order_id,
        state=payment.state,
        quote=cost_service.quote_for_order_amount(order.quoted_amount_cents),
        created_at=payment.created_at,
    )


@router.post(
    "/orders/{order_id}/direct-intent",
    response_model=DirectPaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create legacy direct payment intent",
    description="Legacy compatibility route for direct onchain payment intents. Formal stablecoin checkout should use the HSP route instead.",
    deprecated=True,
)
def create_direct_payment_intent(
    order_id: str,
    payload: DirectPaymentIntentRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
    container: Container = Depends(get_dependency_container),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
) -> DirectPaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payload.currency.upper()
    if currency not in {"USDC", "USDT", "PWR"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")
    if currency in HSP_STABLECOIN_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Direct stablecoin checkout is legacy-only; use the HSP payment intent route",
        )
    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Direct payment amount must match quoted order amount",
        )
    if order.is_cancelled or order.is_expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not payable")

    quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    pwr_amount_wei = _pwr_quote_to_wei_int(quote.pwr_quote) if currency == "PWR" else None

    _ensure_onchain_order_anchor(
        order=order,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
        db=db,
        unresolved_wallet_detail="Buyer wallet address unresolved for direct payment",
        gross_amount_override=pwr_amount_wei,
    )

    payment = Payment(
        order_id=order.id,
        provider="onchain_router",
        amount_cents=payload.amount_cents,
        currency=currency,
        state=PaymentState.PENDING,
    )
    db.add(payment)
    db.flush()

    if currency == "PWR":
        direct_intent = order_writer.build_direct_payment_intent(
            order,
            payment,
            pwr_amount=str(pwr_amount_wei),
            pricing_version=quote.pricing_version,
            pwr_anchor_price_cents=quote.pwr_anchor_price_cents,
        )
    else:
        direct_intent = order_writer.build_direct_payment_intent(order, payment)
    payment.provider_reference = direct_intent.method_name
    payment.merchant_order_id = order.id
    payment.flow_id = payment.id
    registry = ContractsRegistry()
    signing_request = _build_direct_signing_request(
        currency=currency,
        buyer_wallet_address=payload.wallet_address,
        amount_cents=order.quoted_amount_cents,
        token_address=str(direct_intent.payload.get("token_address") or registry.payment_token(currency)),
        router_address=direct_intent.contract_address,
        settlement_escrow=registry.settlement_controller().contract_address,
        chain_id=direct_intent.chain_id,
        permit2_address=registry.permit2().contract_address,
    )
    finalize_required = signing_request is not None
    calldata = None
    wallet_submit_payload = None
    if not finalize_required:
        calldata, wallet_submit_payload = _direct_wallet_submit_payload(direct_intent)
    payment.provider_payload = {
        "direct_intent_payload": direct_intent.payload,
        "signing_request": signing_request,
    }
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return DirectPaymentIntentResponse(
        payment_id=payment.id,
        order_id=order.id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
        finalize_required=finalize_required,
        signing_request=signing_request,
        submit_payload=wallet_submit_payload,
        calldata=calldata,
        state=payment.state,
        quote=quote,
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/finalize-intent", response_model=DirectPaymentFinalizeResponse)
def finalize_direct_payment_intent(
    payment_id: str,
    payload: DirectPaymentFinalizeRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> DirectPaymentFinalizeResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "onchain_router":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an onchain router payment")

    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payment.currency.upper()
    quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    if currency == "PWR":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PWR direct payments do not require finalize")
    direct_intent = order_writer.build_direct_payment_intent(order, payment)
    if payment.provider_payload and payment.provider_payload.get("direct_intent_payload"):
        direct_intent.payload.update(dict(payment.provider_payload["direct_intent_payload"]))

    signing_request = payment.provider_payload.get("signing_request") if payment.provider_payload else None
    if signing_request is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Direct payment signing request not found")

    if currency == "USDC":
        message = signing_request["message"]
        v, r, s = _split_signature(payload.signature)
        direct_intent.payload.update(
            {
                "valid_after": message["validAfter"],
                "valid_before": message["validBefore"],
                "nonce": message["nonce"],
                "v": v,
                "r": r,
                "s": s,
            }
        )
    else:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")

    calldata, wallet_submit_payload = _direct_wallet_submit_payload(direct_intent)
    return DirectPaymentFinalizeResponse(
        payment_id=payment.id,
        order_id=order.id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
        finalize_required=False,
        signing_request=None,
        submit_payload=wallet_submit_payload,
        calldata=calldata,
        state=payment.state,
        quote=quote,
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/sync-onchain", response_model=DirectPaymentSyncResponse)
def sync_onchain_payment(
    payment_id: str,
    payload: DirectPaymentSyncRequest,
    db: Session = Depends(get_db),
    verifier: OnchainPaymentVerifier = Depends(get_onchain_payment_verifier),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> DirectPaymentSyncResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "onchain_router":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an onchain router payment")
    if payload.state == PaymentState.CREATED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sync state must be pending or terminal")

    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    verification = verifier.verify_payment(
        tx_hash=payload.tx_hash,
        wallet_address=payload.wallet_address,
        order=order,
        payment=payment,
    )
    if not verification.matched:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain evidence verification failed: {verification.reason or 'mismatch'}",
        )
    if verification.state != PaymentState.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onchain receipt did not confirm a successful payment",
        )

    db.refresh(order)
    db.refresh(payment)

    if (
        order.onchain_order_id is None
        or order.create_order_tx_hash is None
        or order.create_order_event_id is None
        or order.create_order_block_number is None
    ):
        _backfill_order_chain_anchor_from_verification(order, verification)
    _mark_authoritative_paid_projection(order, order_status="PAID", event_id=verification.event_id)
    _persist_order_tx_correlation(order, payment_tx_hash=verification.tx_hash)
    db.add(order)

    payment.callback_event_id = verification.event_id
    payment.callback_state = verification.state.value
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = verification.tx_hash
    _apply_payment_state(payment, state=PaymentState.SUCCEEDED, db=db, order_writer=order_writer, write_chain=False)
    db.add(payment)
    db.commit()
    return DirectPaymentSyncResponse(
        payment_id=payment.id,
        state=payment.state,
        tx_hash=verification.tx_hash,
        synced_onchain=True,
    )


@router.post("/{payment_id}/sync-hsp", response_model=HSPPaymentSyncResponse)
def sync_hsp_payment_status(
    payment_id: str,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
) -> HSPPaymentSyncResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "hsp":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an HSP payment")

    polled, remote_status = sync_hsp_payment(
        payment,
        db=db,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
    )
    db.refresh(payment)
    return HSPPaymentSyncResponse(
        payment_id=payment.id,
        state=payment.state,
        remote_status=remote_status,
        callback_event_id=payment.callback_event_id,
        polled=polled,
    )


@router.post("/{payment_id}/mock-confirm", response_model=MockPaymentConfirmResponse)
def mock_confirm_payment(
    payment_id: str,
    payload: MockPaymentConfirmRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> MockPaymentConfirmResponse:
    _ensure_demo_write_allowed(detail="Mock payment confirmation is only available in dev/test")
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payload.state not in {PaymentState.SUCCEEDED, PaymentState.FAILED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mock confirmation only accepts succeeded or failed",
        )

    _apply_payment_state(payment, state=payload.state, db=db, order_writer=order_writer)
    db.commit()
    return MockPaymentConfirmResponse(payment_id=payment.id, state=payment.state)
