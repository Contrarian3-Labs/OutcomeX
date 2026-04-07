from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState
from app.domain.models import Machine, Order, Payment, utc_now
from app.domain.rules import has_sufficient_payment, is_dividend_eligible
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt
from app.integrations.onchain_payment_verifier import OnchainPaymentVerifier, get_onchain_payment_verifier
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import encode_contract_call
from app.runtime.cost_service import RuntimeCostService, get_runtime_cost_service
from app.schemas.payment import (
    DirectPaymentIntentRequest,
    DirectPaymentIntentResponse,
    DirectPaymentSyncRequest,
    DirectPaymentSyncResponse,
    MockPaymentConfirmRequest,
    MockPaymentConfirmResponse,
    PaymentIntentRequest,
    PaymentIntentResponse,
)

router = APIRouter()
PWR_WEI_MULTIPLIER = Decimal("1000000000000000000")


TERMINAL_PAYMENT_STATES = {PaymentState.SUCCEEDED, PaymentState.FAILED, PaymentState.REFUNDED}


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


@router.post("/orders/{order_id}/intent", response_model=PaymentIntentResponse, status_code=status.HTTP_201_CREATED)
def create_payment_intent(
    order_id: str,
    payload: PaymentIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> PaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HSP payment amount must match quoted order amount",
        )

    existing_payment = _existing_active_hsp_payment(order.id, db)
    if existing_payment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active HSP payment already exists for this order",
        )

    merchant_order = container.hsp_adapter.create_payment_intent(
        order_id=order.id,
        amount_cents=payload.amount_cents,
        currency=payload.currency.upper(),
    )
    payment = Payment(
        order_id=order.id,
        provider=merchant_order.provider,
        provider_reference=merchant_order.provider_reference,
        merchant_order_id=merchant_order.merchant_order_id,
        flow_id=merchant_order.flow_id,
        checkout_url=merchant_order.payment_url,
        amount_cents=payload.amount_cents,
        currency=payload.currency.upper(),
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
)
def create_direct_payment_intent(
    order_id: str,
    payload: DirectPaymentIntentRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> DirectPaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payload.currency.upper()
    if currency not in {"USDC", "USDT", "PWR"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")
    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Direct payment amount must match quoted order amount",
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

    quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    if currency == "PWR":
        direct_intent = order_writer.build_direct_payment_intent(
            order,
            payment,
            pwr_amount=_pwr_quote_to_wei_string(quote.pwr_quote),
            pricing_version=quote.pricing_version,
            pwr_anchor_price_cents=quote.pwr_anchor_price_cents,
        )
    else:
        direct_intent = order_writer.build_direct_payment_intent(order, payment)
    payment.provider_reference = direct_intent.method_name
    payment.merchant_order_id = order.id
    payment.flow_id = payment.id
    db.add(payment)
    db.commit()
    db.refresh(payment)
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

    return DirectPaymentIntentResponse(
        payment_id=payment.id,
        order_id=order.id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
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
    order_writer: OrderWriter = Depends(get_order_writer),
    verifier: OnchainPaymentVerifier = Depends(get_onchain_payment_verifier),
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

    _backfill_order_chain_anchor_from_verification(order, verification)
    db.add(order)

    _apply_payment_state(
        payment,
        state=verification.state,
        db=db,
        order_writer=order_writer,
        write_chain=False,
    )

    payment.callback_event_id = verification.event_id
    payment.callback_state = verification.state.value
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = verification.tx_hash
    db.add(payment)
    db.commit()
    return DirectPaymentSyncResponse(
        payment_id=payment.id,
        state=payment.state,
        tx_hash=verification.tx_hash,
        synced_onchain=True,
    )


@router.post("/{payment_id}/mock-confirm", response_model=MockPaymentConfirmResponse)
def mock_confirm_payment(
    payment_id: str,
    payload: MockPaymentConfirmRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> MockPaymentConfirmResponse:
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
