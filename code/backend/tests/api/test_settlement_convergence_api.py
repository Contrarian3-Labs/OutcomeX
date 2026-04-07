import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "settlement-convergence.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _seed_confirmed_paid_order() -> tuple[str, str]:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(
            id="machine-1",
            display_name="node-1",
            owner_user_id="owner-1",
            has_active_tasks=True,
            has_unsettled_revenue=False,
        )
        order = Order(
            id="order-1",
            user_id="user-1",
            machine_id=machine.id,
            chat_session_id="chat-1",
            user_prompt="deliver",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
            state=OrderState.RESULT_CONFIRMED,
            result_confirmed_at=datetime.now(timezone.utc),
            settlement_state=SettlementState.NOT_READY,
            settlement_beneficiary_user_id="owner-1",
            settlement_is_self_use=False,
            settlement_is_dividend_eligible=True,
        )
        payment = Payment(
            id="payment-1",
            order_id=order.id,
            provider="onchain_router",
            amount_cents=1000,
            currency="USDC",
            state=PaymentState.SUCCEEDED,
        )
        db.add(machine)
        db.add(order)
        db.add(payment)
        db.commit()
    return "machine-1", "order-1"


def test_start_settlement_only_creates_locked_record_without_revenue_projection(client: TestClient) -> None:
    machine_id, order_id = _seed_confirmed_paid_order()

    response = client.post(f"/api/v1/settlement/orders/{order_id}/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order_id
    assert payload["state"] == "locked"

    container = get_container()
    with container.session_factory() as db:
        order = db.get(Order, order_id)
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order_id).first()
        revenue_entries = db.query(RevenueEntry).filter(RevenueEntry.order_id == order_id).all()
        machine = db.get(Machine, machine_id)
        assert order is not None
        assert settlement is not None
        assert settlement.state == SettlementState.LOCKED
        assert order.settlement_state == SettlementState.LOCKED
        assert revenue_entries == []
        assert machine is not None
        assert machine.has_active_tasks is True
        assert machine.has_unsettled_revenue is False


def test_distribute_revenue_requires_projection_and_then_returns_existing_projection(client: TestClient) -> None:
    machine_id, order_id = _seed_confirmed_paid_order()

    missing = client.post(f"/api/v1/revenue/orders/{order_id}/distribute")
    assert missing.status_code == 409
    assert missing.json()["detail"] == "Settlement projection pending from indexed onchain events"

    container = get_container()
    with container.session_factory() as db:
        settlement = SettlementRecord(
            id="settlement-1",
            order_id=order_id,
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            state=SettlementState.DISTRIBUTED,
            distributed_at=datetime.now(timezone.utc),
        )
        entry = RevenueEntry(
            id="entry-1",
            order_id=order_id,
            settlement_id=settlement.id,
            machine_id=machine_id,
            beneficiary_user_id="owner-1",
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            is_self_use=False,
            is_dividend_eligible=True,
        )
        order = db.get(Order, order_id)
        order.settlement_state = SettlementState.DISTRIBUTED
        db.add(settlement)
        db.add(entry)
        db.add(order)
        db.commit()

    response = client.post(f"/api/v1/revenue/orders/{order_id}/distribute")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order_id
    assert payload["settlement_id"] == "settlement-1"
    assert payload["machine_id"] == machine_id
    assert payload["beneficiary_user_id"] == "owner-1"
    assert payload["machine_share_cents"] == 900
