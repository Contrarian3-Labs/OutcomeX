from datetime import datetime

from pydantic import BaseModel, Field


class MachineCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    owner_user_id: str = Field(min_length=1, max_length=64)


class MachineResponse(BaseModel):
    id: str
    display_name: str
    owner_user_id: str
    has_active_tasks: bool
    has_unsettled_revenue: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MachineTransferRequest(BaseModel):
    new_owner_user_id: str = Field(min_length=1, max_length=64)
    keep_previous_setup: bool = True


class MachineTransferResponse(BaseModel):
    machine_id: str
    previous_owner_user_id: str
    new_owner_user_id: str
    setup_carried_over: bool

