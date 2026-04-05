from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PaymentState
from app.schemas.quote import QuoteResponse


class PaymentIntentRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)


class PaymentIntentResponse(BaseModel):
    payment_id: str
    order_id: str
    provider: str
    provider_reference: str
    checkout_url: str
    flow_id: str | None = None
    merchant_order_id: str | None = None
    state: PaymentState
    quote: QuoteResponse | None = None
    created_at: datetime


class MockPaymentConfirmRequest(BaseModel):
    state: PaymentState


class MockPaymentConfirmResponse(BaseModel):
    payment_id: str
    state: PaymentState


class DirectPaymentIntentRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=8)


class DirectPaymentIntentResponse(BaseModel):
    payment_id: str
    order_id: str
    provider: str
    contract_name: str
    contract_address: str
    chain_id: int
    method_name: str
    signing_standard: str
    submit_payload: dict
    state: PaymentState
    quote: QuoteResponse | None = None
    created_at: datetime


class DirectPaymentSyncRequest(BaseModel):
    state: PaymentState
    tx_hash: str = Field(min_length=3)
    wallet_address: str | None = None


class DirectPaymentSyncResponse(BaseModel):
    payment_id: str
    state: PaymentState
    tx_hash: str
    synced_onchain: bool
