import os
from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import ChatPlan, ExecutionRun, Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.main import create_app


class _ExecutionServiceStub:
    def __init__(self) -> None:
        self.cancelled_run_ids: list[str] = []

    def cancel_run(self, run_id: str):
        self.cancelled_run_ids.append(run_id)
        return None


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, _ExecutionServiceStub]:
    db_path = tmp_path / "smoke-admin.db"
    output_root = tmp_path / "execution-output"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT"] = str(output_root)
    os.environ["OUTCOMEX_ENV"] = "test"
    reset_settings_cache()
    reset_container_cache()

    app = create_app()
    stub = _ExecutionServiceStub()
    app.dependency_overrides[get_agentskillos_execution_service] = lambda: stub
    with TestClient(app) as test_client:
        yield test_client, stub

    reset_settings_cache()
    reset_container_cache()


def _seed_state() -> None:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(id="machine-1", display_name="Node", owner_user_id="owner-1", onchain_machine_id="1", has_active_tasks=True, has_unsettled_revenue=True)
        order = Order(
            id="order-1",
            machine_id=machine.id,
            onchain_machine_id="1",
            onchain_order_id="9",
            user_id="user-1",
            chat_session_id="chat-1",
            user_prompt="demo",
            recommended_plan_summary="summary",
            quoted_amount_cents=1000,
            state=OrderState.EXECUTING,
            execution_state=ExecutionState.RUNNING,
            preview_state=PreviewState.GENERATING,
            settlement_state=SettlementState.NOT_READY,
        )
        payment = Payment(
            id="payment-1",
            order_id=order.id,
            amount_cents=1000,
            currency="PWR",
            state=PaymentState.SUCCEEDED,
        )
        settlement = SettlementRecord(
            id="settlement-1",
            order_id=order.id,
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            state=SettlementState.DISTRIBUTED,
            distributed_at=datetime.now(timezone.utc),
        )
        revenue = RevenueEntry(
            id="revenue-1",
            order_id=order.id,
            settlement_id=settlement.id,
            machine_id=machine.id,
            beneficiary_user_id="owner-1",
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            is_self_use=False,
            is_dividend_eligible=True,
        )
        run = ExecutionRun(
            id="run-1",
            order_id=order.id,
            external_order_id=order.id,
            status=ExecutionRunStatus.RUNNING,
        )
        plan = ChatPlan(id="chat-plan-1", chat_session_id="chat-1", user_id="user-1", user_message="demo", recommended_plan_summary="summary")
        db.add_all([machine, order, payment, settlement, revenue, run, plan])
        db.commit()


def test_smoke_reset_clears_backend_state_and_cancels_active_runs(client: tuple[TestClient, _ExecutionServiceStub], tmp_path) -> None:
    test_client, stub = client
    _seed_state()
    output_root = Path(os.environ["OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT"])
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "stale.txt").write_text("stale", encoding="utf-8")

    response = test_client.post("/api/v1/debug/smoke-reset")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cancelled_execution_runs"] == ["run-1"]
    assert payload["cleared_output_root"] is True
    assert payload["rows_before_reset"] == {
        "machines": 1,
        "orders": 1,
        "payments": 1,
        "execution_runs": 1,
        "settlements": 1,
        "revenue_entries": 1,
        "chat_plans": 1,
    }

    container = get_container()
    with container.session_factory() as db:
        assert db.query(Machine).count() == 0
        assert db.query(Order).count() == 0
        assert db.query(Payment).count() == 0
        assert db.query(ExecutionRun).count() == 0
        assert db.query(SettlementRecord).count() == 0
        assert db.query(RevenueEntry).count() == 0
        assert db.query(ChatPlan).count() == 0

    assert stub.cancelled_run_ids == ["run-1"]
    assert not output_root.exists()


def test_smoke_reset_rejected_outside_dev_or_test(tmp_path) -> None:
    db_path = tmp_path / "prod.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ENV"] = "prod"
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as test_client:
        response = test_client.post("/api/v1/debug/smoke-reset")

    assert response.status_code == 403
    assert response.json()["detail"] == "Smoke reset endpoint is only available in dev/test"
