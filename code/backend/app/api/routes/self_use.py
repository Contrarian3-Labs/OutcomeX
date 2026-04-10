import re
from pathlib import Path
from mimetypes import guess_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
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
_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _normalize_wallet_address(wallet_address: str) -> str:
    if not _EVM_ADDRESS_RE.match(wallet_address):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid viewer wallet address")
    return wallet_address.lower()


def _resolve_owner_machine(*, db: Session, machine_id: str, viewer_wallet_address: str) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    owner_wallet_address = (machine.owner_chain_address or "").lower()
    if not owner_wallet_address or owner_wallet_address != _normalize_wallet_address(viewer_wallet_address):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")
    return machine


def _self_use_external_order_id(*, machine_id: str, viewer_wallet_address: str) -> str:
    return f"self-use:{machine_id}:{_normalize_wallet_address(viewer_wallet_address)}"


def _resolve_self_use_run(
    *,
    db: Session,
    run_id: str,
    viewer_wallet_address: str,
) -> tuple[ExecutionRun, Machine, str]:
    run = db.get(ExecutionRun, run_id)
    if run is None or run.run_kind != _RUN_KIND_SELF_USE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Self-use run not found")
    normalized_viewer_wallet = _normalize_wallet_address(viewer_wallet_address)
    if run.viewer_user_id != normalized_viewer_wallet:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")

    machine = db.get(Machine, run.machine_id) if run.machine_id is not None else None
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    if (machine.owner_chain_address or "").lower() != normalized_viewer_wallet:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-use is owner-only")
    return run, machine, normalized_viewer_wallet


@router.post("/plans", response_model=SelfUsePlansResponse)
def create_self_use_plans(
    payload: SelfUsePlansRequest,
    db: Session = Depends(get_db),
) -> SelfUsePlansResponse:
    normalized_viewer_wallet = _normalize_wallet_address(payload.viewer_wallet_address)
    machine = _resolve_owner_machine(
        db=db,
        machine_id=payload.machine_id,
        viewer_wallet_address=normalized_viewer_wallet,
    )
    recommended_plans = build_recommended_plans(
        user_id=normalized_viewer_wallet,
        chat_session_id=_self_use_external_order_id(
            machine_id=machine.id,
            viewer_wallet_address=normalized_viewer_wallet,
        ),
        user_message=payload.prompt,
        preferred_strategy=payload.execution_strategy,
        input_files=tuple(payload.input_files),
    )
    top_plan = recommended_plans[0]
    return SelfUsePlansResponse(
        viewer_wallet_address=normalized_viewer_wallet,
        machine_id=machine.id,
        prompt=payload.prompt,
        execution_strategy=payload.execution_strategy,
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
    normalized_viewer_wallet = _normalize_wallet_address(payload.viewer_wallet_address)
    machine = _resolve_owner_machine(
        db=db,
        machine_id=payload.machine_id,
        viewer_wallet_address=normalized_viewer_wallet,
    )
    recommended_plans = build_recommended_plans(
        user_id=normalized_viewer_wallet,
        chat_session_id=_self_use_external_order_id(
            machine_id=machine.id,
            viewer_wallet_address=normalized_viewer_wallet,
        ),
        user_message=payload.prompt,
        preferred_strategy=payload.execution_strategy,
        input_files=tuple(payload.input_files),
    )
    selected_plan = select_recommended_plan(
        recommended_plans,
        selected_plan_id=payload.selected_plan_id,
        execution_strategy=payload.execution_strategy,
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
    if (
        payload.selected_native_plan_index is not None
        and selected_plan.native_plan_index is not None
        and payload.selected_native_plan_index != selected_plan.native_plan_index
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected native plan index does not match selected plan",
        )
    external_order_id = _self_use_external_order_id(
        machine_id=machine.id,
        viewer_wallet_address=normalized_viewer_wallet,
    )
    dispatch_context = {"machine_id": machine.id}
    if selected_native_plan_index is not None:
        dispatch_context["selected_native_plan_index"] = str(selected_native_plan_index)

    dispatch = ExecutionEngineService(execution_service=execution_service).dispatch(
        IntentRequest(
            intent_id=external_order_id,
            prompt=payload.prompt,
            input_files=tuple(payload.input_files),
            execution_strategy=ExecutionStrategy(selected_plan.strategy.value),
            context=dispatch_context,
        )
    )
    if not dispatch.accepted or dispatch.run_id is None or dispatch.run_status is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")

    run_status = ExecutionRunStatus(dispatch.run_status.value)
    submission_payload: dict[str, object] = {
        "intent": payload.prompt,
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
        viewer_user_id=normalized_viewer_wallet,
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
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run, _, _ = _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    return build_execution_run_response(run, snapshot, None)


@router.get("/runs/{run_id}/artifacts/file")
def read_self_use_artifact_file(
    run_id: str,
    path: str = Query(min_length=1),
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
):
    run, _, _ = _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    allowed_paths = {
        str(item.get("path"))
        for item in [*(getattr(snapshot, "preview_manifest", ()) or ()), *(getattr(snapshot, "artifact_manifest", ()) or ())]
        if isinstance(item, dict) and item.get("path")
    }
    if path not in allowed_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    run_dir_raw = getattr(snapshot, "run_dir", None) or run.run_dir
    if not run_dir_raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact source unavailable")
    run_dir = Path(run_dir_raw).resolve()
    candidate = (run_dir / path).resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Artifact path is outside run root") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing")

    media_type, _ = guess_type(candidate.name)
    return FileResponse(candidate, media_type=media_type or "application/octet-stream", filename=candidate.name)
