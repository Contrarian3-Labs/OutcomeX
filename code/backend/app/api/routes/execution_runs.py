from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import ExecutionRunStatus, ExecutionState
from app.domain.models import ExecutionRun, Order
from app.indexer.execution_sync import _sync_model_from_snapshot, _sync_order_from_snapshot
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.schemas.execution_run import ExecutionRunResponse

router = APIRouter()



def _selected_plan_payload(snapshot, order: Order | None, submission_payload: dict | None = None) -> dict | None:
    selected_plan = getattr(snapshot, "selected_plan", None)
    if selected_plan:
        payload = dict(selected_plan)
    elif order is None:
        return None
    else:
        metadata = dict(order.execution_metadata or {})
        name = metadata.get("selected_native_plan_name")
        description = metadata.get("selected_native_plan_description")
        nodes = metadata.get("selected_native_plan_nodes")
        if not (name or description or nodes):
            return None
        payload = {
            "index": metadata.get("selected_native_plan_index"),
            "name": name,
            "description": description,
            "nodes": nodes or [],
        }

    if payload.get("index") is None:
        submission_payload = submission_payload or {}
        if submission_payload.get("selected_plan_index") is not None:
            payload["index"] = submission_payload.get("selected_plan_index")
        elif order is not None:
            payload["index"] = dict(order.execution_metadata or {}).get("selected_native_plan_index")
    return payload


def _selected_plan_binding(selected_plan: dict | None, submission_payload: dict | None) -> dict | None:
    submission_payload = submission_payload or {}
    submission_index = submission_payload.get("selected_plan_index")
    selected_index = selected_plan.get("index") if selected_plan else None
    if submission_index is None and selected_index is None:
        return None
    return {
        "submission_payload_selected_plan_index": submission_index,
        "selected_plan_index": selected_index,
        "is_consistent": submission_index == selected_index,
    }


def _build_execution_run_response(run: ExecutionRun, snapshot, order: Order | None) -> ExecutionRunResponse:
    response = ExecutionRunResponse.model_validate(run)
    selected_plan = _selected_plan_payload(snapshot, order, run.submission_payload)
    return response.model_copy(
        update={
            "selected_plan": selected_plan,
            "selected_plan_binding": _selected_plan_binding(selected_plan, run.submission_payload),
            "pid": getattr(snapshot, "pid", None),
            "pid_alive": getattr(snapshot, "pid_alive", None),
            "stdout_log_path": getattr(snapshot, "stdout_log_path", None),
            "stderr_log_path": getattr(snapshot, "stderr_log_path", None),
            "events_log_path": getattr(snapshot, "events_log_path", None),
            "last_heartbeat_at": getattr(snapshot, "last_heartbeat_at", None),
            "current_phase": getattr(snapshot, "current_phase", None),
            "current_step": getattr(snapshot, "current_step", None),
        }
    )


@router.get("/{run_id}", response_model=ExecutionRunResponse)
def get_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> ExecutionRunResponse:
    run = db.get(ExecutionRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    snapshot = execution_service.get_run(run_id)
    _sync_model_from_snapshot(run, snapshot)

    order = db.get(Order, run.order_id)
    if order is not None:
        _sync_order_from_snapshot(
            db=db,
            order=order,
            run=run,
            onchain_lifecycle=onchain_lifecycle,
            order_writer=order_writer,
        )

    db.add(run)
    db.commit()
    db.refresh(run)
    return _build_execution_run_response(run, snapshot, order)


@router.post("/{run_id}/cancel", response_model=ExecutionRunResponse)
def cancel_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRunResponse:
    run = db.get(ExecutionRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    snapshot = execution_service.cancel_run(run_id)
    _sync_model_from_snapshot(run, snapshot)
    order = db.get(Order, run.order_id)
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
    return _build_execution_run_response(run, snapshot, order)
