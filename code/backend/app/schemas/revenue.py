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

