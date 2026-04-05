from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import ExecutionState, OrderState, PreviewState, SettlementState


class OrderCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=36)
    chat_session_id: str = Field(min_length=1, max_length=64)
    user_prompt: str = Field(min_length=1)
    quoted_amount_cents: int = Field(gt=0)


class OrderResponse(BaseModel):
    id: str
    user_id: str
    machine_id: str
    chat_session_id: str
    user_prompt: str
    recommended_plan_summary: str
    quoted_amount_cents: int
    state: OrderState
    execution_state: ExecutionState
    preview_state: PreviewState
    settlement_state: SettlementState
    settlement_beneficiary_user_id: str | None
    settlement_is_self_use: bool | None
    settlement_is_dividend_eligible: bool | None
    execution_metadata: dict[str, str] | None
    result_confirmed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResultConfirmResponse(BaseModel):
    order_id: str
    state: OrderState
    settlement_state: SettlementState
    result_confirmed_at: datetime


class ResultReadyResponse(BaseModel):
    order_id: str
    state: OrderState
    execution_state: ExecutionState
    preview_state: PreviewState

