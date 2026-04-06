from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState
from app.domain.models import Base, ExecutionRun, Machine, Order
from app.indexer.execution_sync import sync_execution_runs_once
from app.integrations.agentskillos_execution_service import ExecutionRunSnapshot


class StubExecutionService:
    def __init__(self, snapshots: dict[str, ExecutionRunSnapshot]) -> None:
        self._snapshots = snapshots

    def get_run(self, run_id: str) -> ExecutionRunSnapshot:
        if run_id not in self._snapshots:
            raise FileNotFoundError(run_id)
        return self._snapshots[run_id]


def test_sync_execution_runs_once_promotes_succeeded_run_and_releases_machine() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    now = datetime.now(timezone.utc)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
        )
        run = ExecutionRun(
            id="run-1",
            order_id="o-1",
            external_order_id="o-1",
            status=ExecutionRunStatus.RUNNING,
            submission_payload={"intent": "build"},
        )
        db.add(machine)
        db.add(order)
        db.add(run)
        db.commit()

    service = StubExecutionService(
        snapshots={
            "run-1": ExecutionRunSnapshot(
                run_id="run-1",
                external_order_id="o-1",
                status=ExecutionRunStatus.SUCCEEDED,
                record_path="/tmp/run-1.json",
                submission_payload={"intent": "build"},
                workspace_path="/tmp/workspace",
                run_dir="/tmp/run-dir",
                preview_manifest=(),
                artifact_manifest=(),
                skills_manifest=(),
                model_usage_manifest=(),
                summary_metrics={},
                started_at=now,
                finished_at=now,
            )
        }
    )

    outcome = sync_execution_runs_once(
        session_factory=session_factory,
        execution_service=service,
    )

    assert outcome.scanned_runs == 1
    assert outcome.synced_runs == 1
    assert outcome.terminal_runs == 1
    assert outcome.missing_snapshots == 0

    with session_factory() as db:
        run = db.get(ExecutionRun, "run-1")
        order = db.get(Order, "o-1")
        machine = db.get(Machine, "m-1")
        assert run.status == ExecutionRunStatus.SUCCEEDED
        assert order.state == OrderState.RESULT_PENDING_CONFIRMATION
        assert order.execution_state == ExecutionState.SUCCEEDED
        assert order.preview_state == PreviewState.READY
        assert machine.has_active_tasks is False


def test_sync_execution_runs_once_counts_missing_snapshots() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
        )
        run = ExecutionRun(
            id="missing-run",
            order_id="o-1",
            external_order_id="o-1",
            status=ExecutionRunStatus.RUNNING,
            submission_payload={"intent": "build"},
        )
        db.add(machine)
        db.add(order)
        db.add(run)
        db.commit()

    outcome = sync_execution_runs_once(
        session_factory=session_factory,
        execution_service=StubExecutionService(snapshots={}),
    )
    assert outcome.scanned_runs == 1
    assert outcome.synced_runs == 0
    assert outcome.missing_snapshots == 1
