import json
import time
from datetime import datetime
from io import BytesIO
from mimetypes import guess_type
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.execution_contract import (
    build_selected_plan_binding,
    build_selected_plan_payload,
    merge_submission_payload,
)
from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState
from app.domain.models import ExecutionRun, Order
from app.execution.observability import (
    list_log_files,
    read_events_after_seq,
    read_log_chunk,
    resolve_logs_root_path,
)
from app.indexer.execution_sync import _sync_model_from_snapshot
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.schemas.execution_run import (
    ExecutionRunLogFileResponse,
    ExecutionRunPlanCandidateResponse,
    ExecutionRunResponse,
)

router = APIRouter()
_ACTIVE_EXECUTION_STATUSES = {
    ExecutionRunStatus.QUEUED,
    ExecutionRunStatus.PLANNING,
    ExecutionRunStatus.RUNNING,
}


def _coerce_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
        return default
    return default


def _coerce_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _coerce_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalize_plan_candidates(value) -> list[ExecutionRunPlanCandidateResponse]:
    if not isinstance(value, (list, tuple)):
        return []
    candidates: list[ExecutionRunPlanCandidateResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        candidates.append(
            ExecutionRunPlanCandidateResponse(
                index=_coerce_int(item.get("index"), default=0),
                name=_coerce_str(item.get("name"), default=""),
                description=_coerce_str(item.get("description"), default=""),
                strategy=_coerce_str(item.get("strategy"), default=""),
            )
        )
    return candidates


def _normalize_log_files(value) -> list[ExecutionRunLogFileResponse]:
    if not isinstance(value, (list, tuple)):
        return []
    files: list[ExecutionRunLogFileResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        files.append(
            ExecutionRunLogFileResponse(
                kind=_coerce_str(item.get("kind"), default=""),
                name=_coerce_str(item.get("name"), default=""),
                path=_coerce_str(item.get("path"), default=""),
                size=_coerce_int(item.get("size"), default=0),
                updated_at=_coerce_datetime(item.get("updated_at")),
            )
        )
    return files


def _resolve_runtime_log_files(snapshot) -> list[ExecutionRunLogFileResponse]:
    raw = getattr(snapshot, "log_files", None)
    normalized = _normalize_log_files(raw)
    if normalized:
        return normalized
    return _normalize_log_files(
        list_log_files(
            run_dir=getattr(snapshot, "run_dir", None),
            stdout_path=getattr(snapshot, "stdout_log_path", None),
            stderr_path=getattr(snapshot, "stderr_log_path", None),
        )
    )


def _resolve_runtime_logs_root_path(snapshot) -> str | None:
    explicit = _coerce_str(getattr(snapshot, "logs_root_path", None), default="")
    if explicit:
        return explicit
    return resolve_logs_root_path(getattr(snapshot, "run_dir", None))


def _resolve_artifact_manifest_paths(snapshot) -> list[str]:
    paths: list[str] = []
    for item in [*(getattr(snapshot, "preview_manifest", ()) or ()), *(getattr(snapshot, "artifact_manifest", ()) or ())]:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item.get("path")))
    return paths


def _resolve_artifact_source_path(snapshot, fallback_run_dir: str | None, relative_path: str) -> Path:
    allowed_paths = set(_resolve_artifact_manifest_paths(snapshot))
    if relative_path not in allowed_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    run_dir_raw = getattr(snapshot, "run_dir", None) or fallback_run_dir
    if not run_dir_raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact source unavailable")
    run_dir = Path(run_dir_raw).resolve()
    candidate = (run_dir / relative_path).resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Artifact path is outside run root") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing")
    return candidate


def _build_artifact_archive(snapshot, fallback_run_dir: str | None, archive_name: str) -> tuple[bytes, str]:
    paths = _resolve_artifact_manifest_paths(snapshot)
    if not paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No artifacts available")

    buffer = BytesIO()
    included = 0
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for relative_path in paths:
            try:
                source = _resolve_artifact_source_path(snapshot, fallback_run_dir, relative_path)
            except HTTPException:
                continue
            archive.write(source, arcname=relative_path)
            included += 1

    if included == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact files are unavailable")
    buffer.seek(0)
    return buffer.getvalue(), archive_name


def _resolve_preview_manifest_paths(snapshot) -> set[str]:
    paths: set[str] = set()
    for item in getattr(snapshot, "preview_manifest", ()) or ():
        if isinstance(item, dict) and item.get("path"):
            paths.add(str(item.get("path")))
    return paths


def _outputs_unlocked(order: Order | None) -> bool:
    if order is None:
        return True
    if order.result_confirmed_at is not None:
        return True
    return order.state == OrderState.RESULT_CONFIRMED


def _assert_order_output_access(
    *,
    order: Order | None,
    snapshot,
    relative_path: str | None = None,
    inline_preview: bool = False,
) -> None:
    if _outputs_unlocked(order):
        return

    if inline_preview and relative_path:
        preview_paths = _resolve_preview_manifest_paths(snapshot)
        if relative_path in preview_paths:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Outputs unlock after result confirmation",
    )


def _resolve_runtime_event_cursor(snapshot) -> int:
    explicit = _coerce_int(getattr(snapshot, "event_cursor", 0), default=0)
    if explicit > 0:
        return explicit
    return read_events_after_seq(getattr(snapshot, "events_log_path", None), after_seq=0).next_cursor


def _resolve_last_progress_at(snapshot) -> datetime | None:
    explicit = _coerce_datetime(getattr(snapshot, "last_progress_at", None))
    if explicit is not None:
        return explicit
    return _coerce_datetime(getattr(snapshot, "last_heartbeat_at", None))


def _resolve_display_current_step(
    status: ExecutionRunStatus,
    current_step: str | None,
    current_phase: str | None = None,
) -> str | None:
    if current_step:
        return current_step
    normalized_phase = (current_phase or "").strip().lower()
    if normalized_phase == "queued":
        return "Queued"
    if normalized_phase in {
        "starting",
    }:
        return "Starting"
    if normalized_phase in {
        "planning",
        "anchor_inference",
        "skill_discovery",
        "plan_generation",
        "plan_selection",
    }:
        return "Planning"
    if normalized_phase in {"executing", "collecting_artifacts"}:
        return "Running"
    if status == ExecutionRunStatus.QUEUED:
        return "Queued"
    if status == ExecutionRunStatus.PLANNING:
        return "Planning"
    if status == ExecutionRunStatus.RUNNING:
        return "Running"
    if status == ExecutionRunStatus.SUCCEEDED:
        return "Completed"
    if status == ExecutionRunStatus.FAILED:
        return "Failed"
    if status == ExecutionRunStatus.CANCELLED:
        return "Cancelled"
    return None


def _resolve_runtime_log_path(snapshot, file_name: str) -> str | None:
    normalized = file_name.strip()
    if not normalized:
        return None
    for item in _resolve_runtime_log_files(snapshot):
        if item.name == normalized:
            return item.path
    return None


def _get_run_or_404(db: Session, run_id: str) -> ExecutionRun:
    run = db.get(ExecutionRun, run_id)
    if run is None or run.run_kind == "self_use":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    return run


def _snapshot_from_run(run: ExecutionRun):
    return SimpleNamespace(
        status=run.status,
        submission_payload=run.submission_payload,
        workspace_path=run.workspace_path,
        run_dir=run.run_dir,
        preview_manifest=run.preview_manifest,
        artifact_manifest=run.artifact_manifest,
        skills_manifest=run.skills_manifest,
        model_usage_manifest=run.model_usage_manifest,
        summary_metrics=run.summary_metrics,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _format_sse(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_execution_run_response(run: ExecutionRun, snapshot, order: Order | None) -> ExecutionRunResponse:
    machine_id = run.machine_id or (order.machine_id if order is not None else None)
    viewer_user_id = run.viewer_user_id or (order.user_id if order is not None else None)
    viewer_wallet_address = run.viewer_user_id if run.run_kind == "self_use" else None
    snapshot_submission_payload = getattr(snapshot, "submission_payload", None)
    response_submission_payload = merge_submission_payload(
        order=order,
        persisted_payload=run.submission_payload,
        snapshot_payload=snapshot_submission_payload,
    )
    selected_plan = build_selected_plan_payload(
        order=order,
        snapshot_selected_plan=getattr(snapshot, "selected_plan", None),
        submission_payload=response_submission_payload,
    )
    runtime_log_files = _resolve_runtime_log_files(snapshot)
    runtime_logs_root_path = _resolve_runtime_logs_root_path(snapshot)
    runtime_event_cursor = _resolve_runtime_event_cursor(snapshot)
    last_heartbeat_at = _coerce_datetime(getattr(snapshot, "last_heartbeat_at", None))
    last_progress_at = _resolve_last_progress_at(snapshot)
    response = ExecutionRunResponse.model_validate(
        {
            "id": run.id,
            "order_id": run.order_id,
            "machine_id": run.machine_id,
            "viewer_user_id": run.viewer_user_id,
            "viewer_wallet_address": None,
            "run_kind": run.run_kind,
            "external_order_id": run.external_order_id,
            "status": run.status,
            "submission_payload": run.submission_payload,
            "selected_plan": None,
            "selected_plan_binding": None,
            "workspace_path": run.workspace_path,
            "run_dir": run.run_dir,
            "preview_manifest": list(run.preview_manifest or []),
            "artifact_manifest": list(run.artifact_manifest or []),
            "skills_manifest": list(run.skills_manifest or []),
            "model_usage_manifest": list(run.model_usage_manifest or []),
            "summary_metrics": run.summary_metrics or {},
            "error": run.error,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "pid": None,
            "pid_alive": None,
            "stdout_log_path": None,
            "stderr_log_path": None,
            "events_log_path": None,
            "last_heartbeat_at": None,
            "current_phase": None,
            "current_step": None,
            "plan_candidates": [],
            "dag": None,
            "active_node_id": None,
            "logs_root_path": None,
            "log_files": [],
            "event_cursor": 0,
            "last_progress_at": None,
            "stalled": False,
            "stalled_reason": None,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }
    ).model_copy(
        update={
            "machine_id": machine_id,
            "viewer_user_id": None if run.run_kind == "self_use" else viewer_user_id,
            "viewer_wallet_address": viewer_wallet_address,
            "run_kind": run.run_kind or "order",
            "status": getattr(snapshot, "status", run.status),
            "submission_payload": response_submission_payload,
            "workspace_path": getattr(snapshot, "workspace_path", run.workspace_path),
            "run_dir": getattr(snapshot, "run_dir", run.run_dir),
            "preview_manifest": list(getattr(snapshot, "preview_manifest", run.preview_manifest) or []),
            "artifact_manifest": list(getattr(snapshot, "artifact_manifest", run.artifact_manifest) or []),
            "skills_manifest": list(getattr(snapshot, "skills_manifest", run.skills_manifest) or []),
            "model_usage_manifest": list(getattr(snapshot, "model_usage_manifest", run.model_usage_manifest) or []),
            "summary_metrics": getattr(snapshot, "summary_metrics", run.summary_metrics) or {},
            "error": getattr(snapshot, "error", run.error),
            "started_at": getattr(snapshot, "started_at", run.started_at),
            "finished_at": getattr(snapshot, "finished_at", run.finished_at),
        }
    )
    return response.model_copy(
        update={
            "selected_plan": selected_plan,
            "selected_plan_binding": build_selected_plan_binding(
                order=order,
                selected_plan=selected_plan,
                submission_payload=response.submission_payload,
            ),
            "pid": getattr(snapshot, "pid", None),
            "pid_alive": getattr(snapshot, "pid_alive", None),
            "stdout_log_path": getattr(snapshot, "stdout_log_path", None),
            "stderr_log_path": getattr(snapshot, "stderr_log_path", None),
            "events_log_path": getattr(snapshot, "events_log_path", None),
            "last_heartbeat_at": last_heartbeat_at,
            "current_phase": getattr(snapshot, "current_phase", None),
            "current_step": _resolve_display_current_step(
                response.status,
                getattr(snapshot, "current_step", None),
                getattr(snapshot, "current_phase", None),
            ),
            "plan_candidates": _normalize_plan_candidates(getattr(snapshot, "plan_candidates", [])),
            "dag": getattr(snapshot, "dag", None),
            "active_node_id": getattr(snapshot, "active_node_id", None),
            "logs_root_path": runtime_logs_root_path,
            "log_files": runtime_log_files,
            "event_cursor": runtime_event_cursor,
            "last_progress_at": last_progress_at,
            "stalled": _coerce_bool(getattr(snapshot, "stalled", False), default=False),
            "stalled_reason": getattr(snapshot, "stalled_reason", None),
        }
    )


@router.get("", response_model=list[ExecutionRunResponse])
def list_execution_runs(
    machine_id: str | None = Query(default=None),
    run_kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> list[ExecutionRunResponse]:
    query = db.query(ExecutionRun)
    if machine_id:
        query = query.filter(ExecutionRun.machine_id == machine_id)
    if run_kind:
        query = query.filter(ExecutionRun.run_kind == run_kind)
    runs = query.order_by(ExecutionRun.created_at.desc()).all()

    order_ids = [run.order_id for run in runs if run.order_id]
    orders_by_id: dict[str, Order] = {}
    if order_ids:
        orders = db.query(Order).filter(Order.id.in_(order_ids)).all()
        orders_by_id = {order.id: order for order in orders}

    responses: list[ExecutionRunResponse] = []
    for run in runs:
        try:
            snapshot = execution_service.get_run(run.id)
        except Exception:
            snapshot = _snapshot_from_run(run)
        order = orders_by_id.get(run.order_id) if run.order_id else None
        responses.append(build_execution_run_response(run, snapshot, order))
    return responses


@router.get("/{run_id}", response_model=ExecutionRunResponse)
def get_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    order = db.get(Order, run.order_id) if run.order_id is not None else None
    return build_execution_run_response(run, snapshot, order)


@router.get("/{run_id}/events")
def get_execution_run_events(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    result = read_events_after_seq(getattr(snapshot, "events_log_path", None), after_seq=after_seq)
    return {"items": result.items, "next_cursor": result.next_cursor}


@router.get("/{run_id}/stream")
def stream_execution_run_events(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> StreamingResponse:
    _get_run_or_404(db, run_id)

    def event_stream():
        cursor = after_seq
        while True:
            snapshot = execution_service.get_run(run_id)
            result = read_events_after_seq(getattr(snapshot, "events_log_path", None), after_seq=cursor)
            emitted = False
            for item in result.items:
                cursor = max(cursor, _coerce_int(item.get("seq"), default=cursor))
                emitted = True
                yield _format_sse("execution_event", item)
            if snapshot.status not in _ACTIVE_EXECUTION_STATUSES and not emitted:
                break
            if not emitted:
                yield ": keep-alive\n\n"
                time.sleep(0.25)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{run_id}/artifacts/file")
def read_execution_run_artifact_file(
    run_id: str,
    path: str = Query(min_length=1),
    inline_preview: bool = Query(default=False),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
):
    run = _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    order = db.get(Order, run.order_id) if run.order_id else None
    _assert_order_output_access(order=order, snapshot=snapshot, relative_path=path, inline_preview=inline_preview)
    candidate = _resolve_artifact_source_path(snapshot, run.run_dir, path)
    media_type, _ = guess_type(candidate.name)
    if inline_preview and (media_type or "").startswith("image/"):
        return FileResponse(candidate, media_type=media_type or "application/octet-stream", filename=candidate.name)
    if inline_preview:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only image preview is available before confirmation",
        )
    return FileResponse(candidate, media_type=media_type or "application/octet-stream", filename=candidate.name)


@router.get("/{run_id}/artifacts/archive")
def download_execution_run_artifact_archive(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
):
    run = _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    order = db.get(Order, run.order_id) if run.order_id else None
    _assert_order_output_access(order=order, snapshot=snapshot)
    archive_bytes, archive_name = _build_artifact_archive(snapshot, run.run_dir, f"execution-run-{run_id}-outputs.zip")
    headers = {"Content-Disposition": f'attachment; filename="{archive_name}"'}
    return StreamingResponse(iter([archive_bytes]), media_type="application/zip", headers=headers)


@router.get("/{run_id}/logs")
def list_execution_run_logs(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    return {
        "logs_root_path": _resolve_runtime_logs_root_path(snapshot),
        "files": [item.model_dump() for item in _resolve_runtime_log_files(snapshot)],
    }


@router.get("/{run_id}/logs/read")
def read_execution_run_log(
    run_id: str,
    file: str,
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> dict:
    _get_run_or_404(db, run_id)
    snapshot = execution_service.get_run(run_id)
    log_path = _resolve_runtime_log_path(snapshot, file)
    if log_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution log not found")
    result = read_log_chunk(log_path, offset=offset)
    return {"file": result.file, "lines": result.content.splitlines(), "next_offset": result.next_offset}


@router.get("/{run_id}/logs/stream")
def stream_execution_run_log(
    run_id: str,
    file: str,
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> StreamingResponse:
    _get_run_or_404(db, run_id)
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


@router.post("/{run_id}/cancel", response_model=ExecutionRunResponse)
def cancel_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = _get_run_or_404(db, run_id)
    snapshot = execution_service.cancel_run(run_id)
    _sync_model_from_snapshot(run, snapshot)
    order = db.get(Order, run.order_id) if run.order_id is not None else None
    if order is not None:
        order.execution_state = ExecutionState.CANCELLED
        metadata = dict(order.execution_metadata or {})
        metadata["run_id"] = run.id
        metadata["run_status"] = run.status.value
        order.execution_metadata = metadata
        db.add(order)
    db.add(run)
    db.commit()
    db.refresh(run)
    return build_execution_run_response(run, snapshot, order)
