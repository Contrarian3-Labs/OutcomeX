from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Order, Payment
from app.schemas.payment import (
    MockPaymentConfirmRequest,
    MockPaymentConfirmResponse,
    PaymentIntentRequest,
    PaymentIntentResponse,
)

router = APIRouter()


@router.post("/orders/{order_id}/intent", response_model=PaymentIntentResponse, status_code=status.HTTP_201_CREATED)
def create_payment_intent(
    order_id: str,
    payload: PaymentIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
) -> PaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    intent = container.hsp_adapter.create_payment_intent(
        order_id=order.id,
        amount_cents=payload.amount_cents,
        currency=payload.currency.upper(),
    )
    payment = Payment(
        order_id=order.id,
        provider=intent.provider,
        provider_reference=intent.provider_reference,
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
        provider_reference=intent.provider_reference,
        checkout_url=intent.checkout_url,
        state=payment.state,
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/mock-confirm", response_model=MockPaymentConfirmResponse)
def mock_confirm_payment(
    payment_id: str,
    payload: MockPaymentConfirmRequest,
    db: Session = Depends(get_db),
) -> MockPaymentConfirmResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payload.state not in {PaymentState.SUCCEEDED, PaymentState.FAILED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mock confirmation only accepts succeeded or failed",
        )

    payment.state = payload.state
    db.add(payment)
    db.commit()
    return MockPaymentConfirmResponse(payment_id=payment.id, state=payment.state)

