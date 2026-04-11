from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PreviewState
from app.domain.models import Base, ExecutionRun, Machine, Order
from app.indexer.execution_sync import sync_execution_runs_once
from app.onchain.lifecycle_service import BroadcastReceipt
from app.onchain.order_writer import OrderWriteResult


@dataclass(frozen=True)
class _Snapshot:
    status: ExecutionRunStatus
    submission_payload: dict | None = None
    workspace_path: str | None = None
    run_dir: str | None = None
    preview_manifest: tuple[dict, ...] = ()
    artifact_manifest: tuple[dict, ...] = ()
    skills_manifest: tuple[dict, ...] = ()
    model_usage_manifest: tuple[dict, ...] = ()
    summary_metrics: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class _ExecutionServiceStub:
    def get_run(self, run_id: str) -> _Snapshot:
        assert run_id == "run-1"
        return _Snapshot(
            status=ExecutionRunStatus.SUCCEEDED,
            submission_payload={"intent": "demo"},
            artifact_manifest=({"path": "workspace/out.png", "type": "image"},),
            finished_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
        )


class _WriterSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def mark_preview_ready(self, order: Order, *, valid_preview: bool = True) -> OrderWriteResult:
        self.calls.append(order.id)
        return OrderWriteResult(
            tx_hash="0xsynthetic-preview",
            submitted_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="markPreviewReady",
            idempotency_key="key",
            payload={"order_id": order.onchain_order_id, "valid_preview": valid_preview},
        )


class _LifecycleSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> BroadcastReceipt:
        assert user_id == "owner-1"
        self.calls.append(write_result.method_name)
        return BroadcastReceipt(
            tx_hash="0xlive-preview",
            receipt=None,
        )

    def enabled(self) -> bool:
        return True


def test_sync_execution_runs_ignores_preview_ready_nonce_race() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, future=True)

    with session_factory() as db:
        machine = Machine(
            id="machine-1",
            display_name="node-1",
            owner_user_id="owner-1",
            onchain_machine_id="7",
            has_active_tasks=True,
        )
        order = Order(
            id="order-1",
            machine_id=machine.id,
            onchain_machine_id="7",
            onchain_order_id="42",
            user_id="user-1",
            chat_session_id="chat-1",
            user_prompt="make image",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
        )
        run = ExecutionRun(
            id="run-1",
            order_id=order.id,
            external_order_id=order.id,
            status=ExecutionRunStatus.RUNNING,
        )
        db.add(machine)
        db.add(order)
        db.add(run)
        db.commit()

    writer = _WriterSpy()

    class _NonceRaceLifecycle:
        def enabled(self) -> bool:
            return True

        def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> BroadcastReceipt:
            raise RuntimeError("json_rpc_error:{'code': -32003, 'message': 'nonce too low'}")

    sync_execution_runs_once(
        session_factory=session_factory,
        execution_service=_ExecutionServiceStub(),
        onchain_lifecycle=_NonceRaceLifecycle(),
        order_writer=writer,
    )

    with session_factory() as db:
        order = db.get(Order, "order-1")
        machine = db.get(Machine, "machine-1")
        assert order is not None
        assert machine is not None
        assert order.execution_state == ExecutionState.SUCCEEDED
        assert order.preview_state == PreviewState.READY
        assert order.state == OrderState.RESULT_PENDING_CONFIRMATION
        assert order.execution_metadata["run_id"] == "run-1"
        assert order.execution_metadata["run_status"] == "succeeded"
        assert "onchain_preview_ready_tx_hash" not in order.execution_metadata
        assert machine.has_active_tasks is False

    assert writer.calls == ["order-1"]


def test_sync_execution_runs_sends_preview_ready_onchain_when_order_is_anchored() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, future=True)

    with session_factory() as db:
        machine = Machine(
            id="machine-1",
            display_name="node-1",
            owner_user_id="owner-1",
            onchain_machine_id="7",
            has_active_tasks=True,
        )
        order = Order(
            id="order-1",
            machine_id=machine.id,
            onchain_machine_id="7",
            onchain_order_id="42",
            user_id="user-1",
            chat_session_id="chat-1",
            user_prompt="make image",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
        )
        run = ExecutionRun(
            id="run-1",
            order_id=order.id,
            external_order_id=order.id,
            status=ExecutionRunStatus.RUNNING,
        )
        db.add(machine)
        db.add(order)
        db.add(run)
        db.commit()

    writer = _WriterSpy()
    lifecycle = _LifecycleSpy()
    sync_execution_runs_once(
        session_factory=session_factory,
        execution_service=_ExecutionServiceStub(),
        onchain_lifecycle=lifecycle,
        order_writer=writer,
    )

    with session_factory() as db:
        order = db.get(Order, "order-1")
        machine = db.get(Machine, "machine-1")
        assert order is not None
        assert machine is not None
        assert order.execution_state == ExecutionState.SUCCEEDED
        assert order.preview_state == PreviewState.READY
        assert order.state == OrderState.RESULT_PENDING_CONFIRMATION
        assert order.execution_metadata["onchain_preview_ready_tx_hash"] == "0xlive-preview"
        assert machine.has_active_tasks is False

    assert writer.calls == ["order-1"]
    assert lifecycle.calls == ["markPreviewReady"]


def test_sync_execution_runs_preserves_concurrent_projection_metadata() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, future=True)

    with session_factory() as db:
        machine = Machine(
            id="machine-1",
            display_name="node-1",
            owner_user_id="owner-1",
            onchain_machine_id="7",
            has_active_tasks=True,
        )
        order = Order(
            id="order-1",
            machine_id=machine.id,
            onchain_machine_id="7",
            onchain_order_id="42",
            user_id="user-1",
            chat_session_id="chat-1",
            user_prompt="make image",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
        )
        run = ExecutionRun(
            id="run-1",
            order_id=order.id,
            external_order_id=order.id,
            status=ExecutionRunStatus.RUNNING,
        )
        db.add(machine)
        db.add(order)
        db.add(run)
        db.commit()

    writer = _WriterSpy()

    class _ConcurrentProjectionLifecycle(_LifecycleSpy):
        def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> BroadcastReceipt:
            receipt = super().send_as_user(user_id=user_id, write_result=write_result)
            with session_factory() as session:
                projected_order = session.get(Order, "order-1")
                assert projected_order is not None
                metadata = dict(projected_order.execution_metadata or {})
                metadata["authoritative_order_status"] = "PREVIEW_READY"
                metadata["authoritative_order_event_id"] = "evt_preview_ready"
                metadata["concurrent_projection_marker"] = "kept"
                projected_order.execution_metadata = metadata
                session.add(projected_order)
                session.commit()
            return receipt

    lifecycle = _ConcurrentProjectionLifecycle()
    sync_execution_runs_once(
        session_factory=session_factory,
        execution_service=_ExecutionServiceStub(),
        onchain_lifecycle=lifecycle,
        order_writer=writer,
    )

    with session_factory() as db:
        order = db.get(Order, "order-1")
        assert order is not None
        assert order.execution_metadata["run_id"] == "run-1"
        assert order.execution_metadata["run_status"] == "succeeded"
        assert order.execution_metadata["onchain_preview_ready_tx_hash"] == "0xlive-preview"
        assert order.execution_metadata["authoritative_order_status"] == "PREVIEW_READY"
        assert order.execution_metadata["authoritative_order_event_id"] == "evt_preview_ready"
        assert order.execution_metadata["concurrent_projection_marker"] == "kept"

    assert writer.calls == ["order-1"]
    assert lifecycle.calls == ["markPreviewReady"]
