from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.execution.contracts import ExecutionStrategy


class OrderCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=36)
    chat_session_id: str = Field(min_length=1, max_length=64)
    user_prompt: str = Field(min_length=1)
    benchmark_task_id: str | None = None
    quoted_amount_cents: int = Field(gt=0)
    input_files: list[str] = Field(default_factory=list)
    planning_context_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_session_token: str | None = Field(default=None, min_length=1, max_length=256)
    attachment_ids: list[str] = Field(default_factory=list)
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
    quoted_pwr_amount: str | None = None
    quoted_pwr_anchor_price_cents: int | None = None
    quoted_pricing_version: str | None = None
    payment_state: PaymentState
    unpaid_expiry_at: datetime | None
    cancelled_at: datetime | None
    is_expired: bool
    is_cancelled: bool
    machine_is_available: bool | None
    state: OrderState
    execution_state: ExecutionState
    preview_state: PreviewState
    settlement_state: SettlementState
    settlement_beneficiary_user_id: str | None
    settlement_is_self_use: bool | None
    settlement_is_dividend_eligible: bool | None
    execution_request: dict | None
    execution_metadata: dict | None
    latest_success_payment_currency: str | None = None
    result_confirmed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    next_cursor: str | None = None


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
    refund_claim_currency: str | None = None
    refund_claim_amount_cents: int | None = None
    refund_claim_amount_pwr: float | None = None
    refund_claim_pwr_anchor_price_cents: int | None = None


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
