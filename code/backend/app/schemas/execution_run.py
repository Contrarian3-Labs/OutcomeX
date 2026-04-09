from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import ExecutionRunStatus


class ExecutionRunPlanCandidateResponse(BaseModel):
    index: int = 0
    name: str = ""
    description: str = ""
    strategy: str = ""


class ExecutionRunLogFileResponse(BaseModel):
    kind: str = ""
    name: str = ""
    path: str = ""
    size: int = 0
    updated_at: datetime | None = None


class ExecutionRunResponse(BaseModel):
    id: str
    order_id: str | None
    machine_id: str | None = None
    viewer_user_id: str | None = None
    viewer_wallet_address: str | None = None
    run_kind: str
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
    pid: int | None = None
    pid_alive: bool | None = None
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    events_log_path: str | None = None
    last_heartbeat_at: datetime | None = None
    current_phase: str | None = None
    current_step: str | None = None
    plan_candidates: list[ExecutionRunPlanCandidateResponse] = Field(default_factory=list)
    dag: dict | None = None
    active_node_id: str | None = None
    logs_root_path: str | None = None
    log_files: list[ExecutionRunLogFileResponse] = Field(default_factory=list)
    event_cursor: int = 0
    last_progress_at: datetime | None = None
    stalled: bool = False
    stalled_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
