from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.benchmark_solutions import get_benchmark_solution
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


def _resolve_planning_prompt(payload: ChatPlanRequest) -> tuple[str, tuple[str, ...]]:
    if not payload.benchmark_task_id:
        return payload.user_message, tuple(payload.input_files)

    solution = get_benchmark_solution(payload.benchmark_task_id)
    if solution is None:
        return payload.user_message, tuple(payload.input_files)
    return solution.benchmark_prompt, solution.input_files


@router.post("/plans", response_model=ChatPlanResponse)
def create_chat_plan(
    payload: ChatPlanRequest,
    db: Session = Depends(get_db),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> ChatPlanResponse:
    planning_prompt, planning_files = _resolve_planning_prompt(payload)
    planning_context_id = build_planning_context_id(
        input_files=planning_files,
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=tuple(payload.attachment_ids),
    )
    try:
        with resolve_planning_input_files(
            db=db,
            input_files=planning_files,
            attachment_session_id=payload.attachment_session_id,
            attachment_session_token=payload.attachment_session_token,
            attachment_ids=tuple(payload.attachment_ids),
        ) as planning_input_files:
            recommended_plans = build_recommended_plans(
                user_id=payload.user_id,
                chat_session_id=payload.chat_session_id,
                user_message=planning_prompt,
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
            "benchmark_task_id": payload.benchmark_task_id,
            "mode": payload.mode,
            "input_files": list(planning_files),
            "planning_context_id": planning_context_id,
            "attachment_session_id": payload.attachment_session_id,
            "attachment_ids": list(payload.attachment_ids),
            "quote": cost_service.quote_for_prompt(planning_prompt),
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
