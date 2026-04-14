from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PaymentState
from app.schemas.quote import QuoteResponse


class PaymentIntentRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="USDC", min_length=3, max_length=8)


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
    wallet_address: str | None = None


class DirectPaymentIntentResponse(BaseModel):
    payment_id: str
    order_id: str
    provider: str
    contract_name: str
    contract_address: str
    chain_id: int
    method_name: str
    signing_standard: str
    finalize_required: bool = False
    signing_request: dict | None = None
    submit_payload: dict | None = None
    calldata: str | None = None
    state: PaymentState
    quote: QuoteResponse | None = None
    created_at: datetime


class DirectPaymentFinalizeRequest(BaseModel):
    signature: str = Field(min_length=3)


class DirectPaymentFinalizeResponse(BaseModel):
    payment_id: str
    order_id: str
    provider: str
    contract_name: str
    contract_address: str
    chain_id: int
    method_name: str
    signing_standard: str
    finalize_required: bool = False
    signing_request: dict | None = None
    submit_payload: dict
    calldata: str
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


class HSPPaymentSyncResponse(BaseModel):
    payment_id: str
    state: PaymentState
    remote_status: str | None = None
    callback_event_id: str | None = None
    polled: bool = False
