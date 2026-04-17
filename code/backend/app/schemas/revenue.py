from datetime import datetime

from pydantic import BaseModel


class RevenueDistributionResponse(BaseModel):
    order_id: str
    settlement_id: str
    machine_id: str
    beneficiary_user_id: str
    gross_amount_cents: int
    platform_fee_cents: int
    machine_share_cents: int
    machine_share_pwr: float | None = None
    is_self_use: bool
    is_dividend_eligible: bool
    distributed_at: datetime


class RevenueEntryResponse(BaseModel):
    id: str
    order_id: str
    settlement_id: str
    machine_id: str
    beneficiary_user_id: str
    gross_amount_cents: int
    platform_fee_cents: int
    machine_share_cents: int
    claimed_cents: int = 0
    claimable_cents: int = 0
    machine_share_pwr: float | None = None
    claimed_pwr: float | None = None
    claimable_pwr: float | None = None
    is_self_use: bool
    is_dividend_eligible: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RevenueAnalyticsPoint(BaseModel):
    date_key: str
    amount_cents: int
    amount_pwr: float | None = None


class RevenueMachineBreakdownItem(BaseModel):
    machine_id: str
    display_name: str
    total_earned_cents: int
    claimable_cents: int
    total_earned_pwr: float | None = None
    claimable_pwr: float | None = None
    acquisition_price_cents: int


class RevenueAccountAnalyticsResponse(BaseModel):
    owner_user_id: str
    currency: str
    total_earned_cents: int
    claimable_cents: int
    claimed_cents: int
    last_7d_cents: int
    trailing_30d_cents: int
    total_earned_pwr: float | None = None
    claimable_pwr: float | None = None
    claimed_pwr: float | None = None
    last_7d_pwr: float | None = None
    trailing_30d_pwr: float | None = None
    indicative_apr: float
    acquisition_total_cents: int
    pwr_anchor_price_cents: int | None = None
    series_7d: list[RevenueAnalyticsPoint]
    series_30d: list[RevenueAnalyticsPoint]
    series_90d: list[RevenueAnalyticsPoint]
    machine_breakdown: list[RevenueMachineBreakdownItem]


class MachineRevenueClaimResponse(BaseModel):
    machine_id: str
    onchain_machine_id: str
    claimant_user_id: str
    tx_hash: str | None = None
    mode: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    contract_name: str | None = None
    method_name: str | None = None
    submit_payload: dict | None = None
    calldata: str | None = None


class WithdrawHistoryItem(BaseModel):
    id: str
    machine_id: str | None
    amount_cents: int
    amount_pwr: float | None = None
    tx_hash: str | None
    claimed_at: datetime

    model_config = {"from_attributes": True}


class RevenueClaimHistoryItem(BaseModel):
    id: str
    claim_kind: str
    claimant_user_id: str | None
    account_address: str
    token_address: str | None
    currency: str | None
    amount_cents: int
    amount_pwr: float | None = None
    tx_hash: str | None
    machine_id: str | None
    claimed_at: datetime

    model_config = {"from_attributes": True}


class PaymentLedgerItem(BaseModel):
    payment_id: str
    order_id: str
    user_prompt: str
    provider: str
    provider_reference: str | None
    currency: str
    amount_cents: int
    state: str
    tx_hash: str | None
    created_at: datetime


class RevenueAccountOverviewResponse(BaseModel):
    owner_user_id: str
    currency: str
    paid_cents: int
    projected_cents: int
    claimable_cents: int
    claimed_cents: int
    projected_pwr: float | None = None
    claimable_pwr: float | None = None
    claimed_pwr: float | None = None
    pwr_anchor_price_cents: int | None = None
    withdraw_history: list[WithdrawHistoryItem]

    model_config = {"from_attributes": True}


class PlatformRevenueOverviewResponse(BaseModel):
    currency: str
    projected_cents: int
    claimed_cents: int
    claimable_cents: int
    claim_history: list[RevenueClaimHistoryItem]
