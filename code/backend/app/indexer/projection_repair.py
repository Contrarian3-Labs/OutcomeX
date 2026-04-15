from __future__ import annotations

from sqlalchemy.exc import OperationalError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState
from app.domain.models import ExecutionRun, Machine, Order

ACTIVE_RUN_STATUSES = {
    ExecutionRunStatus.QUEUED,
    ExecutionRunStatus.PLANNING,
    ExecutionRunStatus.RUNNING,
}
TERMINAL_AUTHORITATIVE_ORDER_STATUSES = {"PREVIEW_READY", "CONFIRMED", "REJECTED", "REFUNDED"}


def repair_historical_projections_once(*, session_factory: sessionmaker) -> int:
    repaired = 0
    try:
        with session_factory() as db:
            orders = list(db.scalars(select(Order)))
            for order in orders:
                repaired += _repair_order_projection(db=db, order=order)
            _recompute_machine_activity(db=db)
            db.commit()
    except OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
    return repaired


def _repair_order_projection(*, db: Session, order: Order) -> int:
    metadata = dict(order.execution_metadata or {})
    authoritative_status = str(metadata.get("authoritative_order_status") or "").upper()
    if authoritative_status not in TERMINAL_AUTHORITATIVE_ORDER_STATUSES:
        return 0

    repaired = 0
    if order.preview_state != PreviewState.READY:
        order.preview_state = PreviewState.READY
        repaired += 1
    if authoritative_status in {"PREVIEW_READY", "CONFIRMED", "REJECTED"} and order.execution_state != ExecutionState.SUCCEEDED:
        order.execution_state = ExecutionState.SUCCEEDED
        repaired += 1

    if authoritative_status == "PREVIEW_READY":
        if order.state in {OrderState.PLAN_RECOMMENDED, OrderState.USER_CONFIRMED, OrderState.EXECUTING}:
            order.state = OrderState.RESULT_PENDING_CONFIRMATION
            repaired += 1
    elif authoritative_status == "CONFIRMED":
        if order.state != OrderState.RESULT_CONFIRMED:
            order.state = OrderState.RESULT_CONFIRMED
            repaired += 1
    else:
        if order.state != OrderState.CANCELLED:
            order.state = OrderState.CANCELLED
            repaired += 1

    if repaired:
        db.add(order)
    return repaired


def _recompute_machine_activity(*, db: Session) -> None:
    machines = list(db.scalars(select(Machine)))
    runs_by_machine: dict[str, list[ExecutionRun]] = {}
    for run in db.scalars(select(ExecutionRun).where(ExecutionRun.machine_id.is_not(None))):
        runs_by_machine.setdefault(str(run.machine_id), []).append(run)

    for machine in machines:
        has_active_order = any(
            order.state in {OrderState.USER_CONFIRMED, OrderState.EXECUTING}
            for order in machine.orders
        )
        has_active_run = any(
            run.status in ACTIVE_RUN_STATUSES
            for order in machine.orders
            for run in order.execution_runs
        ) or any(
            run.status in ACTIVE_RUN_STATUSES
            for run in runs_by_machine.get(machine.id, [])
        )
        machine.has_active_tasks = has_active_order or has_active_run
        db.add(machine)
