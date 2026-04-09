from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.execution_contract import (
    build_selected_plan_binding,
    build_selected_plan_payload,
    merge_submission_payload,
)
from app.domain.enums import ExecutionRunStatus, ExecutionState
from app.domain.models import ExecutionRun, Order
from app.indexer.execution_sync import _sync_model_from_snapshot
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.schemas.execution_run import ExecutionRunResponse

router = APIRouter()


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
    response = ExecutionRunResponse.model_validate(run).model_copy(
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
            "last_heartbeat_at": getattr(snapshot, "last_heartbeat_at", None),
            "current_phase": getattr(snapshot, "current_phase", None),
            "current_step": getattr(snapshot, "current_step", None),
            "plan_candidates": list(getattr(snapshot, "plan_candidates", []) or []),
            "dag": getattr(snapshot, "dag", None),
            "active_node_id": getattr(snapshot, "active_node_id", None),
            "logs_root_path": getattr(snapshot, "logs_root_path", None),
            "log_files": list(getattr(snapshot, "log_files", []) or []),
            "event_cursor": _coerce_int(getattr(snapshot, "event_cursor", 0), default=0),
            "last_progress_at": getattr(snapshot, "last_progress_at", None),
            "stalled": _coerce_bool(getattr(snapshot, "stalled", False), default=False),
            "stalled_reason": getattr(snapshot, "stalled_reason", None),
        }
    )


@router.get("/{run_id}", response_model=ExecutionRunResponse)
def get_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = db.get(ExecutionRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    if run.run_kind == "self_use":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    snapshot = execution_service.get_run(run_id)
    order = db.get(Order, run.order_id) if run.order_id is not None else None
    return build_execution_run_response(run, snapshot, order)


@router.post("/{run_id}/cancel", response_model=ExecutionRunResponse)
def cancel_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = db.get(ExecutionRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    if run.run_kind == "self_use":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

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
