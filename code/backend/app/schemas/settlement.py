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

