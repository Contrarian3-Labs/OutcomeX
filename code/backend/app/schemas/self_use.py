from pydantic import BaseModel, Field

from app.execution.contracts import ExecutionStrategy
from app.schemas.chat_plan import RecommendedPlanResponse


class SelfUsePlansRequest(BaseModel):
    viewer_wallet_address: str = Field(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$")
    machine_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1)
    execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY
    input_files: list[str] = Field(default_factory=list)
    attachment_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_session_token: str | None = Field(default=None, min_length=1, max_length=256)
    attachment_ids: list[str] = Field(default_factory=list)


class SelfUsePlansResponse(BaseModel):
    viewer_wallet_address: str
    machine_id: str
    prompt: str
    execution_strategy: ExecutionStrategy
    input_files: list[str] = Field(default_factory=list)
    planning_context_id: str = ""
    attachment_session_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    recommended_plan_summary: str
    recommended_plans: list[RecommendedPlanResponse] = Field(default_factory=list)


class SelfUseRunCreateRequest(BaseModel):
    viewer_wallet_address: str = Field(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$")
    machine_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1)
    execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY
    input_files: list[str] = Field(default_factory=list)
    planning_context_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_session_token: str | None = Field(default=None, min_length=1, max_length=256)
    attachment_ids: list[str] = Field(default_factory=list)
    selected_plan_id: str | None = None
    selected_native_plan_index: int | None = None
