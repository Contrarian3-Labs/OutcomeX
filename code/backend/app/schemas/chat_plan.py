from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PreviewState
from app.execution.contracts import ExecutionStrategy
from app.schemas.quote import QuoteResponse


class ChatPlanRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    chat_session_id: str = Field(min_length=1, max_length=64)
    user_message: str = Field(min_length=1)
    benchmark_task_id: str | None = None
    mode: ExecutionStrategy | None = None
    input_files: list[str] = Field(default_factory=list)


class RecommendedPlanResponse(BaseModel):
    plan_id: str
    strategy: ExecutionStrategy
    title: str
    summary: str
    why_this_plan: str
    tradeoff: str
    native_plan_index: int | None = None
    native_plan_name: str = ""
    native_plan_description: str = ""


class ChatPlanResponse(BaseModel):
    id: str
    user_id: str
    chat_session_id: str
    user_message: str
    benchmark_task_id: str | None = None
    mode: ExecutionStrategy | None = None
    input_files: list[str] = Field(default_factory=list)
    recommended_plan_summary: str
    recommended_plans: list[RecommendedPlanResponse] = Field(default_factory=list)
    preview_state: PreviewState
    quote: QuoteResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
