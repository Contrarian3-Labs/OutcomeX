from datetime import datetime

from pydantic import BaseModel, Field


class MachineCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    owner_user_id: str = Field(min_length=1, max_length=64)
    onchain_machine_id: str | None = Field(default=None, min_length=1, max_length=64)


class MachineRuntimeSnapshotResponse(BaseModel):
    used_capacity_units: int
    total_capacity_units: int
    capacity_utilization: float
    used_memory_mb: int
    total_memory_mb: int
    memory_utilization: float
    running_count: int
    queued_count: int
    queue_utilization: float
    max_concurrency: int
    max_queue_depth: int


class MachineResponse(BaseModel):
    id: str
    onchain_machine_id: str | None
    display_name: str
    owner_user_id: str
    owner_chain_address: str | None
    ownership_source: str
    owner_projection_last_event_id: str | None
    owner_projected_at: datetime | None
    pending_transfer_new_owner_user_id: str | None
    has_active_tasks: bool
    has_unsettled_revenue: bool
    transfer_ready: bool
    transfer_blocking_reasons: list[str]
    projected_cents: int
    claimed_cents: int
    claimable_cents: int
    profile_label: str
    gpu_spec: str
    supported_categories: list[str]
    hosted_by: str
    availability: int
    confirmed_revenue_30d_pwr: float
    claimable_pwr: float
    indicative_apr: float
    runtime_snapshot: MachineRuntimeSnapshotResponse
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MachineTransferRequest(BaseModel):
    new_owner_user_id: str = Field(min_length=1, max_length=64)
    keep_previous_setup: bool = True


class MachineTransferResponse(BaseModel):
    machine_id: str
    previous_owner_user_id: str
    canonical_owner_user_id: str
    new_owner_user_id: str
    setup_carried_over: bool
    transfer_status: str
    owner_updated: bool
