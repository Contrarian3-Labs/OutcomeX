import os

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState, SettlementState
from app.domain.models import Base
from app.domain.models import ExecutionRun, Machine, Order
from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.indexer.projection_repair import repair_historical_projections_once


@pytest.fixture
def session_factory(tmp_path):
    db_path = tmp_path / "projection-repair.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "false"
    reset_settings_cache()
    reset_container_cache()
    container = get_container()
    Base.metadata.create_all(bind=container.engine)
    yield sessionmaker(bind=container.engine, autocommit=False, autoflush=False, future=True)
    reset_settings_cache()
    reset_container_cache()


def _create_machine(db, *, machine_id: str = "machine-1") -> Machine:
    machine = Machine(
        id=machine_id,
        display_name="Node",
        owner_user_id="owner-1",
        has_active_tasks=True,
    )
    db.add(machine)
    db.flush()
    return machine


def _create_order(
    db,
    *,
    order_id: str = "order-1",
    machine_id: str = "machine-1",
    state: OrderState = OrderState.PLAN_RECOMMENDED,
    execution_state: ExecutionState = ExecutionState.QUEUED,
    preview_state: PreviewState = PreviewState.DRAFT,
    authoritative_status: str = "PREVIEW_READY",
) -> Order:
    order = Order(
        id=order_id,
        user_id="buyer-1",
        machine_id=machine_id,
        chat_session_id="chat-1",
        user_prompt="prompt",
        recommended_plan_summary="plan",
        quoted_amount_cents=100,
        state=state,
        execution_state=execution_state,
        preview_state=preview_state,
        settlement_state=SettlementState.NOT_READY,
        execution_metadata={
            "reconstructed_from_chain": True,
            "authoritative_order_status": authoritative_status,
        },
    )
    db.add(order)
    db.flush()
    return order


def test_projection_repair_releases_preview_ready_machine_and_marks_execution_succeeded(session_factory) -> None:
    with session_factory() as db:
        _create_machine(db)
        _create_order(db)
        db.commit()

    repaired = repair_historical_projections_once(session_factory=session_factory)

    assert repaired == 3
    with session_factory() as db:
        order = db.get(Order, "order-1")
        machine = db.get(Machine, "machine-1")
        assert order is not None
        assert machine is not None
        assert order.state == OrderState.RESULT_PENDING_CONFIRMATION
        assert order.execution_state == ExecutionState.SUCCEEDED
        assert order.preview_state == PreviewState.READY
        assert machine.has_active_tasks is False


def test_projection_repair_marks_confirmed_order_succeeded(session_factory) -> None:
    with session_factory() as db:
        _create_machine(db)
        _create_order(
            db,
            state=OrderState.RESULT_PENDING_CONFIRMATION,
            execution_state=ExecutionState.QUEUED,
            preview_state=PreviewState.DRAFT,
            authoritative_status="CONFIRMED",
        )
        db.commit()

    repaired = repair_historical_projections_once(session_factory=session_factory)

    assert repaired == 3
    with session_factory() as db:
        order = db.get(Order, "order-1")
        assert order is not None
        assert order.state == OrderState.RESULT_CONFIRMED
        assert order.execution_state == ExecutionState.SUCCEEDED
        assert order.preview_state == PreviewState.READY


def test_projection_repair_keeps_machine_active_when_live_run_exists(session_factory) -> None:
    with session_factory() as db:
        _create_machine(db)
        _create_order(db)
        db.add(
            ExecutionRun(
                id="run-1",
                order_id="order-1",
                machine_id="machine-1",
                external_order_id="ext-1",
                status=ExecutionRunStatus.RUNNING,
            )
        )
        db.commit()

    repair_historical_projections_once(session_factory=session_factory)

    with session_factory() as db:
        machine = db.get(Machine, "machine-1")
        assert machine is not None
        assert machine.has_active_tasks is True
