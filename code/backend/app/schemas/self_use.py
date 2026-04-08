from pydantic import BaseModel, Field

from app.execution.contracts import ExecutionStrategy
from app.schemas.chat_plan import RecommendedPlanResponse


class SelfUsePlansRequest(BaseModel):
    viewer_user_id: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1)
    execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY
    input_files: list[str] = Field(default_factory=list)


class SelfUsePlansResponse(BaseModel):
    viewer_user_id: str
    machine_id: str
    prompt: str
    execution_strategy: ExecutionStrategy
    input_files: list[str] = Field(default_factory=list)
    recommended_plan_summary: str
    recommended_plans: list[RecommendedPlanResponse] = Field(default_factory=list)


class SelfUseRunCreateRequest(BaseModel):
    viewer_user_id: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1)
    execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY
    input_files: list[str] = Field(default_factory=list)
    selected_plan_id: str | None = None
    selected_native_plan_index: int | None = None
