from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.execution_runs import build_execution_run_response
from app.domain.enums import ExecutionRunStatus
from app.domain.models import ExecutionRun, Machine
from app.domain.planning import build_recommended_plans, select_recommended_plan
from app.execution import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.schemas.chat_plan import RecommendedPlanResponse
from app.schemas.execution_run import ExecutionRunResponse
from app.schemas.self_use import SelfUsePlansRequest, SelfUsePlansResponse, SelfUseRunCreateRequest

router = APIRouter()

_RUN_KIND_SELF_USE = "self_use"


def _resolve_owner_machine(*, db: Session, machine_id: str, viewer_user_id: str) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    if machine.owner_user_id != viewer_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")
    return machine


def _self_use_external_order_id(*, machine_id: str, viewer_user_id: str) -> str:
    return f"self-use:{machine_id}:{viewer_user_id}"


@router.post("/plans", response_model=SelfUsePlansResponse)
def create_self_use_plans(
    payload: SelfUsePlansRequest,
    db: Session = Depends(get_db),
) -> SelfUsePlansResponse:
    machine = _resolve_owner_machine(db=db, machine_id=payload.machine_id, viewer_user_id=payload.viewer_user_id)
    recommended_plans = build_recommended_plans(
        user_id=payload.viewer_user_id,
        chat_session_id=_self_use_external_order_id(machine_id=machine.id, viewer_user_id=payload.viewer_user_id),
        user_message=payload.user_message,
        preferred_strategy=payload.mode,
        input_files=tuple(payload.input_files),
    )
    top_plan = recommended_plans[0]
    return SelfUsePlansResponse(
        viewer_user_id=payload.viewer_user_id,
        machine_id=machine.id,
        user_message=payload.user_message,
        mode=payload.mode,
        input_files=list(payload.input_files),
        recommended_plan_summary=top_plan.summary,
        recommended_plans=[
            RecommendedPlanResponse(
                plan_id=plan.plan_id,
                strategy=plan.strategy,
                title=plan.title,
                summary=plan.summary,
                why_this_plan=plan.why_this_plan,
                tradeoff=plan.tradeoff,
                native_plan_index=plan.native_plan_index,
                native_plan_name=plan.native_plan_name,
                native_plan_description=plan.native_plan_description,
            )
            for plan in recommended_plans
        ],
    )


@router.post("/runs", response_model=ExecutionRunResponse, status_code=status.HTTP_201_CREATED)
def create_self_use_run(
    payload: SelfUseRunCreateRequest,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    machine = _resolve_owner_machine(db=db, machine_id=payload.machine_id, viewer_user_id=payload.viewer_user_id)
    recommended_plans = build_recommended_plans(
        user_id=payload.viewer_user_id,
        chat_session_id=_self_use_external_order_id(machine_id=machine.id, viewer_user_id=payload.viewer_user_id),
        user_message=payload.user_prompt,
        preferred_strategy=payload.mode,
        input_files=tuple(payload.input_files),
    )
    selected_plan = select_recommended_plan(
        recommended_plans,
        selected_plan_id=payload.selected_plan_id,
        execution_strategy=payload.mode,
    )
    if selected_plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected plan is invalid for this request",
        )

    selected_native_plan_index = (
        payload.selected_native_plan_index
        if payload.selected_native_plan_index is not None
        else selected_plan.native_plan_index
    )
    external_order_id = _self_use_external_order_id(machine_id=machine.id, viewer_user_id=payload.viewer_user_id)
    dispatch_context = {"machine_id": machine.id}
    if selected_native_plan_index is not None:
        dispatch_context["selected_native_plan_index"] = str(selected_native_plan_index)

    dispatch = ExecutionEngineService(execution_service=execution_service).dispatch(
        IntentRequest(
            intent_id=external_order_id,
            prompt=payload.user_prompt,
            input_files=tuple(payload.input_files),
            execution_strategy=ExecutionStrategy(selected_plan.strategy.value),
            context=dispatch_context,
        )
    )
    if not dispatch.accepted or dispatch.run_id is None or dispatch.run_status is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")

    run_status = ExecutionRunStatus(dispatch.run_status.value)
    submission_payload: dict[str, object] = {
        "intent": payload.user_prompt,
        "files": list(payload.input_files),
        "execution_strategy": selected_plan.strategy.value,
        "selected_plan_id": selected_plan.plan_id,
        "selected_plan_strategy": selected_plan.strategy.value,
    }
    if selected_native_plan_index is not None:
        submission_payload["selected_plan_index"] = selected_native_plan_index
    run = ExecutionRun(
        id=dispatch.run_id,
        order_id=None,
        machine_id=machine.id,
        viewer_user_id=payload.viewer_user_id,
        run_kind=_RUN_KIND_SELF_USE,
        external_order_id=external_order_id,
        status=run_status,
        submission_payload=submission_payload,
        workspace_path=None,
        run_dir=None,
        preview_manifest=[],
        artifact_manifest=[],
        skills_manifest=[],
        model_usage_manifest=[],
        summary_metrics={},
        error=None,
        started_at=None,
        finished_at=None,
    )
    machine.has_active_tasks = True
    run = db.merge(run)
    db.add(machine)
    db.commit()
    db.refresh(run)
    return build_execution_run_response(run, dispatch, None)


@router.get("/runs/{run_id}", response_model=ExecutionRunResponse)
def get_self_use_run(
    run_id: str,
    viewer_user_id: str = Query(min_length=1, max_length=64),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = db.get(ExecutionRun, run_id)
    if run is None or run.run_kind != _RUN_KIND_SELF_USE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Self-use run not found")
    if run.viewer_user_id != viewer_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")
    machine = db.get(Machine, run.machine_id) if run.machine_id is not None else None
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    if machine.owner_user_id != viewer_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")

    snapshot = execution_service.get_run(run_id)
    return build_execution_run_response(run, snapshot, None)
