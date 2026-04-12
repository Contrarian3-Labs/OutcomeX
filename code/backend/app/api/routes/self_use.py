import re
import time
from pathlib import Path
from mimetypes import guess_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.execution_runs import (
    _ACTIVE_EXECUTION_STATUSES,
    _build_artifact_archive,
    _format_sse,
    _resolve_artifact_source_path,
    _resolve_runtime_log_files,
    _resolve_runtime_log_path,
    _resolve_runtime_logs_root_path,
    build_execution_run_response,
)
from app.domain.enums import ExecutionRunStatus
from app.domain.models import ExecutionRun, Machine
from app.domain.planning import build_recommended_plans, select_recommended_plan
from app.execution import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.schemas.chat_plan import RecommendedPlanResponse
from app.schemas.execution_run import ExecutionRunResponse
from app.schemas.self_use import SelfUsePlansRequest, SelfUsePlansResponse, SelfUseRunCreateRequest
from app.execution.observability import read_events_after_seq, read_log_chunk
from app.services.attachments import (
    AttachmentResolutionError,
    build_planning_context_id,
    resolve_planning_input_files,
    stage_bound_execution_input_files,
)

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
    planning_context_id = build_planning_context_id(
        input_files=tuple(payload.input_files),
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=tuple(payload.attachment_ids),
    )
    machine = _resolve_owner_machine(
        db=db,
        machine_id=payload.machine_id,
        viewer_wallet_address=normalized_viewer_wallet,
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
                user_id=normalized_viewer_wallet,
                chat_session_id=_self_use_external_order_id(
                    machine_id=machine.id,
                    viewer_wallet_address=normalized_viewer_wallet,
                ),
                user_message=payload.prompt,
                preferred_strategy=payload.execution_strategy,
                input_files=planning_input_files,
                planning_context_key=planning_context_id,
            )
    except AttachmentResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    top_plan = recommended_plans[0]
    return SelfUsePlansResponse(
        viewer_wallet_address=normalized_viewer_wallet,
        machine_id=machine.id,
        prompt=payload.prompt,
        execution_strategy=payload.execution_strategy,
        input_files=list(payload.input_files),
        planning_context_id=planning_context_id,
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=list(payload.attachment_ids),
        recommended_plan_summary=top_plan.summary,
        recommended_plans=[
            RecommendedPlanResponse(
                plan_id=plan.plan_id,
                planning_context_id=planning_context_id,
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
    derived_planning_context_id = build_planning_context_id(
        input_files=tuple(payload.input_files),
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=tuple(payload.attachment_ids),
    )
    if (
        payload.planning_context_id
        and (payload.input_files or payload.attachment_session_id or payload.attachment_ids)
        and payload.planning_context_id != derived_planning_context_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="planning_context_id does not match input_files/attachment references",
        )
    planning_context_id = payload.planning_context_id or derived_planning_context_id
    machine = _resolve_owner_machine(
        db=db,
        machine_id=payload.machine_id,
        viewer_wallet_address=normalized_viewer_wallet,
    )
    selected_plan = None
    dispatch = None
    selected_native_plan_index = None
    resolved_input_files: tuple[str, ...] = tuple(payload.input_files)
    try:
        with resolve_planning_input_files(
            db=db,
            input_files=tuple(payload.input_files),
            attachment_session_id=payload.attachment_session_id,
            attachment_session_token=payload.attachment_session_token,
            attachment_ids=tuple(payload.attachment_ids),
        ) as planning_input_files:
            recommended_plans = build_recommended_plans(
                user_id=normalized_viewer_wallet,
                chat_session_id=_self_use_external_order_id(
                    machine_id=machine.id,
                    viewer_wallet_address=normalized_viewer_wallet,
                ),
                user_message=payload.prompt,
                preferred_strategy=payload.execution_strategy,
                input_files=planning_input_files,
                planning_context_key=planning_context_id,
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
            dispatch_context["planning_context_id"] = planning_context_id
            resolved_input_files = stage_bound_execution_input_files(
                db=db,
                input_files=tuple(payload.input_files),
                attachment_session_id=payload.attachment_session_id,
                attachment_ids=tuple(payload.attachment_ids),
            )

            plan_candidates = tuple(
                {
                    "index": plan.native_plan_index if plan.native_plan_index is not None else index,
                    "name": plan.native_plan_name or plan.title,
                    "description": plan.native_plan_description or plan.summary,
                    "strategy": plan.strategy.value,
                }
                for index, plan in enumerate(recommended_plans)
            )
            dispatch = ExecutionEngineService(execution_service=execution_service).dispatch(
                IntentRequest(
                    intent_id=external_order_id,
                    prompt=payload.prompt,
                    input_files=resolved_input_files,
                    execution_strategy=ExecutionStrategy(selected_plan.strategy.value),
                    context=dispatch_context,
                ),
                plan_candidates=plan_candidates,
            )
    except AttachmentResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if selected_plan is None or dispatch is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")
    external_order_id = _self_use_external_order_id(
        machine_id=machine.id,
        viewer_wallet_address=normalized_viewer_wallet,
    )
    if not dispatch.accepted or dispatch.run_id is None or dispatch.run_status is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")

    run_status = ExecutionRunStatus(dispatch.run_status.value)
    submission_payload: dict[str, object] = {
        "intent": payload.prompt,
        "files": list(resolved_input_files),
        "source_input_files": list(payload.input_files),
        "execution_strategy": selected_plan.strategy.value,
        "selected_plan_id": selected_plan.plan_id,
        "selected_plan_strategy": selected_plan.strategy.value,
        "planning_context_id": planning_context_id,
        "planning_attachment_session_id": payload.attachment_session_id,
        "planning_attachment_ids": list(payload.attachment_ids),
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
    snapshot = execution_service.get_run(run.id)
    return build_execution_run_response(run, snapshot, None)


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
    inline_preview: bool = Query(default=False),
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
):
    run, _, _ = _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    candidate = _resolve_artifact_source_path(snapshot, run.run_dir, path)
    media_type, _ = guess_type(candidate.name)
    if inline_preview:
        return FileResponse(
            candidate,
            media_type=media_type or "application/octet-stream",
            filename=candidate.name,
            content_disposition_type="inline",
        )
    return FileResponse(candidate, media_type=media_type or "application/octet-stream", filename=candidate.name)


@router.get("/runs/{run_id}/artifacts/archive")
def download_self_use_artifact_archive(
    run_id: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
):
    run, _, _ = _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    archive_bytes, archive_name = _build_artifact_archive(snapshot, run.run_dir, f"self-use-run-{run_id}-outputs.zip")
    headers = {"Content-Disposition": f'attachment; filename="{archive_name}"'}
    return StreamingResponse(iter([archive_bytes]), media_type="application/zip", headers=headers)


@router.get("/runs/{run_id}/events")
def get_self_use_run_events(
    run_id: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    after_seq: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    result = read_events_after_seq(getattr(snapshot, "events_log_path", None), after_seq=after_seq)
    return {"items": result.items, "next_cursor": result.next_cursor}


@router.get("/runs/{run_id}/stream")
def stream_self_use_run_events(
    run_id: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    after_seq: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> StreamingResponse:
    _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)

    def event_stream():
        cursor = after_seq
        while True:
            snapshot = execution_service.get_run(run_id)
            result = read_events_after_seq(getattr(snapshot, "events_log_path", None), after_seq=cursor)
            emitted = False
            for item in result.items:
                cursor = max(cursor, int(item.get("seq", cursor) or cursor))
                emitted = True
                yield _format_sse("execution_event", item)
            if snapshot.status not in _ACTIVE_EXECUTION_STATUSES and not emitted:
                break
            if not emitted:
                yield ": keep-alive\n\n"
                time.sleep(0.25)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}/logs")
def list_self_use_run_logs(
    run_id: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    return {
        "logs_root_path": _resolve_runtime_logs_root_path(snapshot),
        "files": [item.model_dump() for item in _resolve_runtime_log_files(snapshot)],
    }


@router.get("/runs/{run_id}/logs/read")
def read_self_use_run_log(
    run_id: str,
    file: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    snapshot = execution_service.get_run(run_id)
    log_path = _resolve_runtime_log_path(snapshot, file)
    if log_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution log not found")
    result = read_log_chunk(log_path, offset=offset)
    return {"file": result.file, "lines": result.content.splitlines(), "next_offset": result.next_offset}


@router.get("/runs/{run_id}/logs/stream")
def stream_self_use_run_log(
    run_id: str,
    file: str,
    viewer_wallet_address: str = Query(min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> StreamingResponse:
    _resolve_self_use_run(db=db, run_id=run_id, viewer_wallet_address=viewer_wallet_address)
    initial_snapshot = execution_service.get_run(run_id)
    initial_log_path = _resolve_runtime_log_path(initial_snapshot, file)
    if initial_log_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution log not found")

    def log_stream():
        cursor = offset
        while True:
            snapshot = execution_service.get_run(run_id)
            log_path = _resolve_runtime_log_path(snapshot, file)
            if log_path is None:
                break
            result = read_log_chunk(log_path, offset=cursor)
            emitted = False
            if result.content:
                line_offset = cursor
                for raw_line in result.content.splitlines(keepends=True):
                    line = raw_line.rstrip("\r\n")
                    emitted = True
                    yield _format_sse(
                        "log_line",
                        {
                            "file": result.file,
                            "offset": line_offset,
                            "line": line,
                        },
                    )
                    line_offset += len(raw_line)
                cursor = result.next_offset
            if snapshot.status not in _ACTIVE_EXECUTION_STATUSES and not emitted:
                break
            if not emitted:
                yield ": keep-alive\n\n"
                time.sleep(0.25)

    return StreamingResponse(log_stream(), media_type="text/event-stream")
