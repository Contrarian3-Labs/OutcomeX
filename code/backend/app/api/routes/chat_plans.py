from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.models import ChatPlan
from app.domain.planning import summarize_plan_from_chat
from app.schemas.chat_plan import ChatPlanRequest, ChatPlanResponse

router = APIRouter()


@router.post("/plans", response_model=ChatPlanResponse)
def create_chat_plan(payload: ChatPlanRequest, db: Session = Depends(get_db)) -> ChatPlan:
    plan = ChatPlan(
        user_id=payload.user_id,
        chat_session_id=payload.chat_session_id,
        user_message=payload.user_message,
        recommended_plan_summary=summarize_plan_from_chat(payload.user_message),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan

