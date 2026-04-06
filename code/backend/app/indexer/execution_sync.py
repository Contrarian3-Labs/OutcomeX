"""Background synchronization for AgentSkillOS execution runs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState
from app.domain.models import ExecutionRun, Machine, Order
from app.integrations.agentskillos_execution_service import (
    AgentSkillOSExecutionService,
    get_agentskillos_execution_service,
)
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriter, get_order_writer

ACTIVE_RUN_STATUSES = (
    ExecutionRunStatus.QUEUED,
    ExecutionRunStatus.PLANNING,
    ExecutionRunStatus.RUNNING,
)
TERMINAL_RUN_STATUSES = (
    ExecutionRunStatus.SUCCEEDED,
    ExecutionRunStatus.FAILED,
    ExecutionRunStatus.CANCELLED,
)


@dataclass(frozen=True)
class ExecutionSyncOutcome:
    scanned_runs: int
    synced_runs: int
    terminal_runs: int
    missing_snapshots: int


def sync_execution_runs_once(
    *,
    session_factory: sessionmaker,
    execution_service: AgentSkillOSExecutionService | None = None,
    onchain_lifecycle: OnchainLifecycleService | None = None,
    order_writer: OrderWriter | None = None,
) -> ExecutionSyncOutcome:
    """Synchronize active execution runs from AgentSkillOS snapshots."""

    service = execution_service or get_agentskillos_execution_service()
    lifecycle = onchain_lifecycle or get_onchain_lifecycle_service()
    writer = order_writer or get_order_writer()
    scanned_runs = 0
    synced_runs = 0
    terminal_runs = 0
    missing_snapshots = 0

    with session_factory() as db:
        active_runs = list(
            db.scalars(
                select(ExecutionRun).where(
                    ExecutionRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
        )
        scanned_runs = len(active_runs)
        for run in active_runs:
            try:
                snapshot = service.get_run(run.id)
            except FileNotFoundError:
                missing_snapshots += 1
                continue

            _sync_model_from_snapshot(run, snapshot)
            synced_runs += 1

            order = db.get(Order, run.order_id)
            if order is not None:
                _sync_order_from_snapshot(
                    db=db,
                    order=order,
                    run=run,
                    onchain_lifecycle=lifecycle,
                    order_writer=writer,
                )
            if run.status in TERMINAL_RUN_STATUSES:
                terminal_runs += 1

            db.add(run)

        db.commit()

    return ExecutionSyncOutcome(
        scanned_runs=scanned_runs,
        synced_runs=synced_runs,
        terminal_runs=terminal_runs,
        missing_snapshots=missing_snapshots,
    )


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


def _sync_order_from_snapshot(
    *,
    db: Session,
    order: Order,
    run: ExecutionRun,
    onchain_lifecycle: OnchainLifecycleService,
    order_writer: OrderWriter,
) -> None:
    metadata = dict(order.execution_metadata or {})
    metadata["run_id"] = run.id
    metadata["run_status"] = run.status.value

    if run.status == ExecutionRunStatus.SUCCEEDED:
        _broadcast_preview_ready_if_needed(
            db=db,
            order=order,
            valid_preview=True,
            metadata=metadata,
            onchain_lifecycle=onchain_lifecycle,
            order_writer=order_writer,
        )
        order.execution_state = ExecutionState.SUCCEEDED
        order.preview_state = PreviewState.READY
        if order.state == OrderState.EXECUTING:
            order.state = OrderState.RESULT_PENDING_CONFIRMATION
        _release_machine_active_task(db=db, machine_id=order.machine_id)
    elif run.status in {ExecutionRunStatus.FAILED, ExecutionRunStatus.CANCELLED}:
        _broadcast_failed_preview_refund_if_needed(
            db=db,
            order=order,
            metadata=metadata,
            onchain_lifecycle=onchain_lifecycle,
            order_writer=order_writer,
        )
        order.execution_state = (
            ExecutionState.FAILED
            if run.status == ExecutionRunStatus.FAILED
            else ExecutionState.CANCELLED
        )
        order.state = OrderState.CANCELLED
        _release_machine_active_task(db=db, machine_id=order.machine_id)

    order.execution_metadata = metadata
    db.add(order)


def _release_machine_active_task(*, db: Session, machine_id: str) -> None:
    machine = db.get(Machine, machine_id)
    if machine is None:
        return
    machine.has_active_tasks = False
    db.add(machine)


def _is_nonce_too_low_error(exc: RuntimeError) -> bool:
    return "nonce too low" in str(exc).lower()


def _broadcast_preview_ready_if_needed(
    *,
    db: Session,
    order: Order,
    valid_preview: bool,
    metadata: dict,
    onchain_lifecycle: OnchainLifecycleService,
    order_writer: OrderWriter,
) -> None:
    if not onchain_lifecycle.enabled():
        return
    if not order.onchain_order_id:
        return
    if metadata.get("onchain_preview_ready_tx_hash"):
        return
    machine = db.get(Machine, order.machine_id)
    if machine is None:
        return
    try:
        receipt = onchain_lifecycle.send_as_user(
            user_id=machine.owner_user_id,
            write_result=order_writer.mark_preview_ready(order, valid_preview=valid_preview),
        )
    except RuntimeError as exc:
        if _is_nonce_too_low_error(exc):
            return
        raise
    metadata["onchain_preview_ready_tx_hash"] = receipt.tx_hash


def _broadcast_failed_preview_refund_if_needed(
    *,
    db: Session,
    order: Order,
    metadata: dict,
    onchain_lifecycle: OnchainLifecycleService,
    order_writer: OrderWriter,
) -> None:
    if not onchain_lifecycle.enabled():
        return
    if not order.onchain_order_id:
        return
    machine = db.get(Machine, order.machine_id)
    if machine is None:
        return
    if not metadata.get("onchain_preview_ready_tx_hash"):
        preview_receipt = onchain_lifecycle.send_as_user(
            user_id=machine.owner_user_id,
            write_result=order_writer.mark_preview_ready(order, valid_preview=False),
        )
        metadata["onchain_preview_ready_tx_hash"] = preview_receipt.tx_hash
    if metadata.get("onchain_failed_refund_tx_hash"):
        return
    refund_receipt = onchain_lifecycle.send_as_user(
        user_id=machine.owner_user_id,
        write_result=order_writer.refund_failed_or_no_valid_preview(order),
    )
    metadata["onchain_failed_refund_tx_hash"] = refund_receipt.tx_hash
