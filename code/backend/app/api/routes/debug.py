from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import get_dependency_container
from app.core.container import Container
from app.domain.enums import ExecutionRunStatus
from app.domain.models import Base, ChatPlan, ExecutionRun, Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.integrations.agentskillos_execution_service import (
    AgentSkillOSExecutionService,
    get_agentskillos_execution_service,
)
from app.runtime.hardware_simulator import get_shared_hardware_simulator, reset_shared_hardware_simulator

router = APIRouter()

_ACTIVE_RUN_STATUSES = (
    ExecutionRunStatus.QUEUED,
    ExecutionRunStatus.PLANNING,
    ExecutionRunStatus.RUNNING,
)


def _count_rows(container: Container) -> dict[str, int]:
    with container.session_factory() as db:
        return {
            "machines": int(db.scalar(select(func.count(Machine.id))) or 0),
            "orders": int(db.scalar(select(func.count(Order.id))) or 0),
            "payments": int(db.scalar(select(func.count(Payment.id))) or 0),
            "execution_runs": int(db.scalar(select(func.count(ExecutionRun.id))) or 0),
            "settlements": int(db.scalar(select(func.count(SettlementRecord.id))) or 0),
            "revenue_entries": int(db.scalar(select(func.count(RevenueEntry.id))) or 0),
            "chat_plans": int(db.scalar(select(func.count(ChatPlan.id))) or 0),
        }


def _active_run_ids(container: Container) -> list[str]:
    with container.session_factory() as db:
        return list(
            db.scalars(
                select(ExecutionRun.id).where(ExecutionRun.status.in_(_ACTIVE_RUN_STATUSES))
            )
        )


def _resolve_output_root(container: Container) -> Path:
    root = Path(container.settings.agentskillos_execution_output_root)
    return root if root.is_absolute() else (Path.cwd() / root).resolve()


@router.post('/smoke-reset')
def smoke_reset(
    container: Container = Depends(get_dependency_container),
    execution_service: AgentSkillOSExecutionService = Depends(get_agentskillos_execution_service),
) -> dict:
    if container.settings.env not in {"dev", "test"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Smoke reset endpoint is only available in dev/test',
        )

    rows_before_reset = _count_rows(container)
    cancelled_execution_runs: list[str] = []
    for run_id in _active_run_ids(container):
        try:
            execution_service.cancel_run(run_id)
        except FileNotFoundError:
            pass
        cancelled_execution_runs.append(run_id)

    output_root = _resolve_output_root(container)
    cleared_output_root = output_root.exists()
    if cleared_output_root:
        shutil.rmtree(output_root, ignore_errors=True)

    Base.metadata.drop_all(bind=container.engine)
    Base.metadata.create_all(bind=container.engine)

    reset_shared_hardware_simulator()
    container.hardware_simulator = get_shared_hardware_simulator()

    return {
        'rows_before_reset': rows_before_reset,
        'rows_after_reset': _count_rows(container),
        'cancelled_execution_runs': cancelled_execution_runs,
        'cleared_output_root': cleared_output_root,
    }
