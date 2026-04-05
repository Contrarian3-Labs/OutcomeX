from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import PreviewState
from app.schemas.quote import QuoteResponse


class ChatPlanRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    chat_session_id: str = Field(min_length=1, max_length=64)
    user_message: str = Field(min_length=1)


class ChatPlanResponse(BaseModel):
    id: str
    user_id: str
    chat_session_id: str
    user_message: str
    recommended_plan_summary: str
    preview_state: PreviewState
    quote: QuoteResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
