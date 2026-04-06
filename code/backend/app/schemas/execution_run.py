from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import ExecutionRunStatus


class ExecutionRunResponse(BaseModel):
    id: str
    order_id: str
    external_order_id: str
    status: ExecutionRunStatus
    submission_payload: dict | None = None
    selected_plan: dict | None = None
    selected_plan_binding: dict | None = None
    workspace_path: str | None = None
    run_dir: str | None = None
    preview_manifest: list[dict] = Field(default_factory=list)
    artifact_manifest: list[dict] = Field(default_factory=list)
    skills_manifest: list[dict] = Field(default_factory=list)
    model_usage_manifest: list[dict] = Field(default_factory=list)
    summary_metrics: dict = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
