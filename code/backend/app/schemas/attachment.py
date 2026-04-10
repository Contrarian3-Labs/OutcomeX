from datetime import datetime

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}
