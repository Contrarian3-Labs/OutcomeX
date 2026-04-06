from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import SettlementState


class SettlementPreviewResponse(BaseModel):
    order_id: str
    gross_amount_cents: int
    platform_fee_cents: int
    machine_share_cents: int
    state: SettlementState


class SettlementStartResponse(BaseModel):
    settlement_id: str
    order_id: str
    state: SettlementState
    created_at: datetime


class PlatformRevenueClaimRequest(BaseModel):
    currency: str


class RefundClaimResponse(BaseModel):
    order_id: str
    claimant_user_id: str
    currency: str
    tx_hash: str | None = None
    mode: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    contract_name: str | None = None
    method_name: str | None = None
    submit_payload: dict | None = None
    calldata: str | None = None


class PlatformRevenueClaimResponse(BaseModel):
    currency: str
    tx_hash: str | None = None
    mode: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    contract_name: str | None = None
    method_name: str | None = None
    submit_payload: dict | None = None
    calldata: str | None = None

