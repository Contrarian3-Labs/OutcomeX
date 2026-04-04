from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PaymentState


class PaymentIntentRequest(BaseModel):
    amount_cents: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)


class PaymentIntentResponse(BaseModel):
    payment_id: str
    order_id: str
    provider: str
    provider_reference: str
    checkout_url: str
    state: PaymentState
    created_at: datetime


class MockPaymentConfirmRequest(BaseModel):
    state: PaymentState


class MockPaymentConfirmResponse(BaseModel):
    payment_id: str
    state: PaymentState

