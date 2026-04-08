import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, Order, Payment
from app.main import create_app
from app.onchain.claim_state_reader import get_settlement_claim_state_reader
from app.onchain.lifecycle_service import get_onchain_lifecycle_service


class StubClaimStateReader:
    def __init__(self, amount: int) -> None:
        self.amount = amount
        self.calls: list[tuple[str, str]] = []

    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        self.calls.append((user_id, currency))
        return self.amount

    def platform_accrued_amount(self, *, currency: str) -> int:
        return 0


class EnabledLifecycleService:
    def enabled(self) -> bool:
        return True


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "order-available-actions.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    claim_state_reader = StubClaimStateReader(amount=700)
    app = create_app()
    app.dependency_overrides[get_settlement_claim_state_reader] = lambda: claim_state_reader
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: EnabledLifecycleService()
    with TestClient(app) as test_client:
        yield test_client, claim_state_reader
    reset_settings_cache()
    reset_container_cache()


def _seed_refundable_order() -> str:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(id="machine-1", display_name="node-1", owner_user_id="owner-1")
        order = Order(
            id="order-1",
            user_id="user-1",
            machine_id=machine.id,
            onchain_order_id="77",
            create_order_tx_hash="0xcreate",
            chat_session_id="chat-1",
            user_prompt="deliver",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
            state=OrderState.CANCELLED,
            execution_state=ExecutionState.CANCELLED,
            preview_state=PreviewState.READY,
            settlement_state=SettlementState.DISTRIBUTED,
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
        db.add_all([machine, order, payment])
        db.commit()
    return "order-1"


def test_available_actions_uses_onchain_refund_amount_for_claim_button(client) -> None:
    test_client, claim_state_reader = client
    order_id = _seed_refundable_order()

    response = test_client.get(f"/api/v1/orders/{order_id}/available-actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_claim_refund"] is True
    assert payload["refund_claim_currency"] == "USDC"
    assert payload["refund_claim_amount_cents"] == 700
    assert claim_state_reader.calls == [("user-1", "USDC")]


def test_available_actions_hides_claim_button_when_onchain_refund_is_zero(client) -> None:
    test_client, claim_state_reader = client
    claim_state_reader.amount = 0
    order_id = _seed_refundable_order()

    response = test_client.get(f"/api/v1/orders/{order_id}/available-actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_claim_refund"] is False
    assert payload["refund_claim_currency"] == "USDC"
    assert payload["refund_claim_amount_cents"] == 0
