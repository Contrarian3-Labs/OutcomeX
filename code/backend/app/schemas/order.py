from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import ExecutionState, OrderState, PreviewState, SettlementState
from app.execution.contracts import ExecutionStrategy


class OrderCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=36)
    chat_session_id: str = Field(min_length=1, max_length=64)
    user_prompt: str = Field(min_length=1)
    quoted_amount_cents: int = Field(gt=0)
    input_files: list[str] = Field(default_factory=list)
    execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY
    selected_plan_id: str | None = None


class OrderResponse(BaseModel):
    id: str
    onchain_order_id: str | None
    onchain_machine_id: str | None
    create_order_tx_hash: str | None
    create_order_event_id: str | None
    create_order_block_number: int | None
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
    execution_request: dict | None
    execution_metadata: dict | None
    result_confirmed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResultConfirmResponse(BaseModel):
    order_id: str
    state: OrderState
    settlement_state: SettlementState
    result_confirmed_at: datetime | None = None
    mode: str | None = None
    tx_hash: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    contract_name: str | None = None
    method_name: str | None = None
    submit_payload: dict | None = None
    calldata: str | None = None


class ResultReadyRequest(BaseModel):
    valid_preview: bool = True


class ResultReadyResponse(BaseModel):
    order_id: str
    state: OrderState
    execution_state: ExecutionState
    preview_state: PreviewState


class OrderAvailableActionsResponse(BaseModel):
    order_id: str
    preview_valid: bool | None
    can_confirm_result: bool
    can_reject_valid_preview: bool
    can_refund_failed_or_no_valid_preview: bool
    can_claim_refund: bool


class OrderSettlementActionResponse(BaseModel):
    order_id: str
    state: OrderState
    settlement_state: SettlementState
    mode: str | None = None
    tx_hash: str | None
    chain_id: int | None = None
    contract_address: str | None = None
    contract_name: str | None
    method_name: str | None
    submit_payload: dict | None = None
    calldata: str | None = None
