from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState
from app.domain.models import ExecutionRun, Machine, Order
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.schemas.execution_run import ExecutionRunResponse

router = APIRouter()


def _sync_model_from_snapshot(run: ExecutionRun, snapshot) -> None:
    run.status = snapshot.status
    run.submission_payload = snapshot.submission_payload
    run.workspace_path = snapshot.workspace_path
    run.run_dir = snapshot.run_dir
    run.preview_manifest = list(snapshot.preview_manifest)
    run.artifact_manifest = list(snapshot.artifact_manifest)
    run.skills_manifest = list(snapshot.skills_manifest)
    run.model_usage_manifest = list(snapshot.model_usage_manifest)
    run.summary_metrics = snapshot.summary_metrics or {}
    run.error = snapshot.error
    run.started_at = snapshot.started_at
    run.finished_at = snapshot.finished_at


@router.get("/{run_id}", response_model=ExecutionRunResponse)
def get_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRun:
    run = db.get(ExecutionRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    snapshot = execution_service.get_run(run_id)
    _sync_model_from_snapshot(run, snapshot)

    order = db.get(Order, run.order_id)
    if order is not None:
        metadata = dict(order.execution_metadata or {})
        metadata["run_id"] = run.id
        metadata["run_status"] = run.status.value
        order.execution_metadata = metadata
        if run.status == ExecutionRunStatus.SUCCEEDED:
            order.execution_state = ExecutionState.SUCCEEDED
            order.preview_state = PreviewState.READY
            order.state = OrderState.RESULT_PENDING_CONFIRMATION
            machine = db.get(Machine, order.machine_id)
            if machine is not None:
                machine.has_active_tasks = False
                db.add(machine)
        elif run.status in {ExecutionRunStatus.FAILED, ExecutionRunStatus.CANCELLED}:
            order.execution_state = ExecutionState.FAILED if run.status == ExecutionRunStatus.FAILED else ExecutionState.CANCELLED
            machine = db.get(Machine, order.machine_id)
            if machine is not None:
                machine.has_active_tasks = False
                db.add(machine)
        db.add(order)

    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.post("/{run_id}/cancel", response_model=ExecutionRunResponse)
def cancel_execution_run(
    run_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRun:
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
        machine = db.get(Machine, order.machine_id)
        if machine is not None:
            machine.has_active_tasks = False
            db.add(machine)
        db.add(order)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
