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
    is_self_use: bool
    is_dividend_eligible: bool
    created_at: datetime

    model_config = {"from_attributes": True}


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
    machine_id: str
    amount_cents: int
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
    tx_hash: str | None
    machine_id: str | None
    claimed_at: datetime

    model_config = {"from_attributes": True}


class RevenueAccountOverviewResponse(BaseModel):
    owner_user_id: str
    currency: str
    paid_cents: int
    projected_cents: int
    claimable_cents: int
    claimed_cents: int
    withdraw_history: list[WithdrawHistoryItem]

    model_config = {"from_attributes": True}
