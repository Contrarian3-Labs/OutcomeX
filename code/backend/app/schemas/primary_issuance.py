from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PaymentState


class PrimaryIssuanceSkuResponse(BaseModel):
    sku_id: str
    display_name: str
    profile_label: str
    gpu_spec: str
    model_family: str
    price_cents: int
    currency: str
    stock_available: int


class PrimaryIssuancePurchaseIntentRequest(BaseModel):
    buyer_user_id: str | None = Field(default=None, min_length=1, max_length=64)
    buyer_wallet_address: str | None = Field(default=None, min_length=1, max_length=128)


class PrimaryIssuancePurchaseIntentResponse(BaseModel):
    purchase_id: str
    sku_id: str
    buyer_user_id: str
    provider: str
    provider_reference: str
    merchant_order_id: str | None = None
    flow_id: str | None = None
    checkout_url: str
    amount_cents: int
    currency: str
    state: PaymentState
    created_at: datetime


class PrimaryIssuancePurchaseSyncResponse(BaseModel):
    purchase_id: str
    state: PaymentState
    remote_status: str | None
    callback_event_id: str | None
    callback_tx_hash: str | None
    minted_machine_id: str | None
    minted_onchain_machine_id: str | None
    polled: bool
