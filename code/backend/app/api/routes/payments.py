from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Machine, Order, Payment, utc_now
from app.domain.rules import has_sufficient_payment, is_dividend_eligible
from app.onchain.order_writer import OrderWriter, get_order_writer
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


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _freeze_settlement_policy_if_fully_paid(order: Order, db: Session) -> bool:
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    if not has_sufficient_payment(order.quoted_amount_cents, paid_cents):
        return False

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if order.settlement_beneficiary_user_id is None:
        dividend_eligible = is_dividend_eligible(order.user_id, machine.owner_user_id)
        order.settlement_beneficiary_user_id = machine.owner_user_id
        order.settlement_is_self_use = not dividend_eligible
        order.settlement_is_dividend_eligible = dividend_eligible
    machine.has_unsettled_revenue = True
    db.add(order)
    db.add(machine)
    return True


def _apply_payment_state(
    payment: Payment,
    *,
    state: PaymentState,
    db: Session,
    order_writer: OrderWriter,
    write_chain: bool = True,
) -> Order:
    payment.state = state
    db.add(payment)
    db.flush()
    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if state == PaymentState.SUCCEEDED and _freeze_settlement_policy_if_fully_paid(order, db) and write_chain:
        order_writer.mark_order_paid(order, payment)
    return order


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
    if currency == "PWR":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PWR direct payment is disabled until anchor exists",
        )
    if currency not in {"USDC", "USDT"}:
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

    direct_intent = order_writer.build_direct_payment_intent(order, payment)
    payment.provider_reference = direct_intent.method_name
    payment.merchant_order_id = order.id
    payment.flow_id = payment.id
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return DirectPaymentIntentResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
        submit_payload=direct_intent.payload,
        state=payment.state,
        quote=cost_service.quote_for_order_amount(order.quoted_amount_cents),
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/sync-onchain", response_model=DirectPaymentSyncResponse)
def sync_onchain_payment(
    payment_id: str,
    payload: DirectPaymentSyncRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> DirectPaymentSyncResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "onchain_router":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an onchain router payment")
    if payload.state == PaymentState.CREATED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sync state must be pending or terminal")

    payment.callback_event_id = f"onchain:{payload.tx_hash.lower()}"
    payment.callback_state = payload.state.value
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = payload.tx_hash
    db.add(payment)

    _apply_payment_state(
        payment,
        state=payload.state,
        db=db,
        order_writer=order_writer,
        write_chain=False,
    )
    db.commit()
    return DirectPaymentSyncResponse(
        payment_id=payment.id,
        state=payment.state,
        tx_hash=payload.tx_hash,
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
