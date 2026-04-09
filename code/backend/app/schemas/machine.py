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


class MachineListingSummaryResponse(BaseModel):
    onchain_listing_id: str
    seller_chain_address: str
    buyer_chain_address: str | None
    payment_token_address: str
    payment_token_symbol: str | None
    payment_token_decimals: int | None
    price_units: int
    state: str
    expires_at: datetime | None
    listed_at: datetime
    cancelled_at: datetime | None
    filled_at: datetime | None


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
    locked_unsettled_revenue_cents: int
    locked_unsettled_revenue_pwr: float
    locked_beneficiary_user_ids: list[str]
    profile_label: str
    gpu_spec: str
    supported_categories: list[str]
    hosted_by: str
    availability: int
    confirmed_revenue_30d_pwr: float
    claimable_pwr: float
    indicative_apr: float
    active_listing: MachineListingSummaryResponse | None = None
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
