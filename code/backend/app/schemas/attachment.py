from datetime import datetime

from pydantic import BaseModel


class AttachmentSessionCreateResponse(BaseModel):
    session_id: str
    session_token: str
    created_at: datetime


class AttachmentResponse(BaseModel):
    id: str
    session_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
