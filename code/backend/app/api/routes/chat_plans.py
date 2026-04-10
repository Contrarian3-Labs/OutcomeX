from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.models import ChatPlan
from app.domain.planning import build_recommended_plans
from app.runtime.cost_service import RuntimeCostService, get_runtime_cost_service
from app.schemas.chat_plan import ChatPlanRequest, ChatPlanResponse, RecommendedPlanResponse
from app.services.attachments import (
    AttachmentResolutionError,
    build_planning_context_id,
    resolve_planning_input_files,
)

router = APIRouter()


@router.post("/plans", response_model=ChatPlanResponse)
def create_chat_plan(
    payload: ChatPlanRequest,
    db: Session = Depends(get_db),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> ChatPlanResponse:
    planning_context_id = build_planning_context_id(
        input_files=tuple(payload.input_files),
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=tuple(payload.attachment_ids),
    )
    try:
        with resolve_planning_input_files(
            db=db,
            input_files=tuple(payload.input_files),
            attachment_session_id=payload.attachment_session_id,
            attachment_session_token=payload.attachment_session_token,
            attachment_ids=tuple(payload.attachment_ids),
        ) as planning_input_files:
            recommended_plans = build_recommended_plans(
                user_id=payload.user_id,
                chat_session_id=payload.chat_session_id,
                user_message=payload.user_message,
                preferred_strategy=payload.mode,
                input_files=planning_input_files,
                planning_context_key=planning_context_id,
            )
    except AttachmentResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    top_plan = recommended_plans[0]
    plan = ChatPlan(
        user_id=payload.user_id,
        chat_session_id=payload.chat_session_id,
        user_message=payload.user_message,
        recommended_plan_summary=top_plan.summary,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    response = ChatPlanResponse.model_validate(plan)
    return response.model_copy(
        update={
            "mode": payload.mode,
            "input_files": list(payload.input_files),
            "planning_context_id": planning_context_id,
            "attachment_session_id": payload.attachment_session_id,
            "attachment_ids": list(payload.attachment_ids),
            "quote": cost_service.quote_for_prompt(payload.user_message),
            "recommended_plans": [
                RecommendedPlanResponse(
                    plan_id=item.plan_id,
                    planning_context_id=planning_context_id,
                    strategy=item.strategy,
                    title=item.title,
                    summary=item.summary,
                    why_this_plan=item.why_this_plan,
                    tradeoff=item.tradeoff,
                    native_plan_index=item.native_plan_index,
                    native_plan_name=item.native_plan_name,
                    native_plan_description=item.native_plan_description,
                )
                for item in recommended_plans
            ],
        }
    )
