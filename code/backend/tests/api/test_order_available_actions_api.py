import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, Order, Payment, SettlementClaimRecord, SettlementRecord
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


def _seed_refundable_order(
    *,
    order_id: str = "order-1",
    payment_id: str = "payment-1",
    settlement_id: str = "settlement-1",
    onchain_order_id: str = "77",
    user_id: str = "user-1",
    gross_amount_cents: int = 1000,
    platform_fee_cents: int = 100,
    machine_share_cents: int = 200,
    payment_currency: str = "USDC",
    provider_payload: dict | None = None,
    created_at: datetime | None = None,
) -> str:
    container = get_container()
    with container.session_factory() as db:
        machine = db.get(Machine, "machine-1")
        if machine is None:
            machine = Machine(id="machine-1", display_name="node-1", owner_user_id="owner-1")
            db.add(machine)
            db.flush()
        order = Order(
            id=order_id,
            user_id=user_id,
            machine_id=machine.id,
            onchain_order_id=onchain_order_id,
            create_order_tx_hash="0xcreate",
            chat_session_id="chat-1",
            user_prompt="deliver",
            recommended_plan_summary="plan",
            quoted_amount_cents=gross_amount_cents,
            state=OrderState.CANCELLED,
            execution_state=ExecutionState.CANCELLED,
            preview_state=PreviewState.READY,
            settlement_state=SettlementState.DISTRIBUTED,
            settlement_beneficiary_user_id="owner-1",
            settlement_is_self_use=False,
            settlement_is_dividend_eligible=True,
            created_at=created_at or datetime.now(timezone.utc),
        )
        payment = Payment(
            id=payment_id,
            order_id=order.id,
            provider="onchain_router",
            amount_cents=gross_amount_cents,
            currency=payment_currency,
            provider_payload=provider_payload,
            state=PaymentState.SUCCEEDED,
        )
        settlement = SettlementRecord(
            id=settlement_id,
            order_id=order.id,
            gross_amount_cents=gross_amount_cents,
            platform_fee_cents=platform_fee_cents,
            machine_share_cents=machine_share_cents,
            state=SettlementState.DISTRIBUTED,
            distributed_at=(created_at or datetime.now(timezone.utc)) + timedelta(minutes=1),
        )
        db.add_all([order, payment, settlement])
        db.commit()
    return order_id


def _insert_refund_claim(*, claimant_user_id: str, amount_cents: int, event_id: str) -> None:
    container = get_container()
    with container.session_factory() as db:
        db.add(
            SettlementClaimRecord(
                id=event_id,
                event_id=event_id,
                claim_kind="refund",
                claimant_user_id=claimant_user_id,
                account_address="0xrefunduser",
                token_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                amount_cents=amount_cents,
                tx_hash=f"0x{event_id}",
                claimed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


def test_available_actions_uses_projected_refund_amount_for_claim_button(client) -> None:
    test_client, claim_state_reader = client
    order_id = _seed_refundable_order()

    response = test_client.get(f"/api/v1/orders/{order_id}/available-actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_claim_refund"] is True
    assert payload["refund_claim_currency"] == "USDC"
    assert payload["refund_claim_amount_cents"] == 700
    assert claim_state_reader.calls == []


def test_available_actions_ignores_account_level_refund_reader_and_uses_local_projection(client) -> None:
    test_client, claim_state_reader = client
    claim_state_reader.amount = 0
    order_id = _seed_refundable_order()

    response = test_client.get(f"/api/v1/orders/{order_id}/available-actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_claim_refund"] is True
    assert payload["refund_claim_currency"] == "USDC"
    assert payload["refund_claim_amount_cents"] == 700
    assert claim_state_reader.calls == []


def test_available_actions_projects_refund_claims_per_order_fifo(client) -> None:
    test_client, claim_state_reader = client
    claim_state_reader.amount = 700
    earlier = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    later = earlier + timedelta(minutes=5)
    older_order_id = _seed_refundable_order(
        order_id="order-older",
        payment_id="payment-older",
        settlement_id="settlement-older",
        onchain_order_id="77",
        gross_amount_cents=1000,
        platform_fee_cents=100,
        machine_share_cents=200,
        created_at=earlier,
    )
    newer_order_id = _seed_refundable_order(
        order_id="order-newer",
        payment_id="payment-newer",
        settlement_id="settlement-newer",
        onchain_order_id="78",
        gross_amount_cents=800,
        platform_fee_cents=150,
        machine_share_cents=150,
        created_at=later,
    )
    _insert_refund_claim(claimant_user_id="user-1", amount_cents=700, event_id="refund-evt-1")

    older_response = test_client.get(f"/api/v1/orders/{older_order_id}/available-actions")
    newer_response = test_client.get(f"/api/v1/orders/{newer_order_id}/available-actions")

    assert older_response.status_code == 200
    assert newer_response.status_code == 200
    older_payload = older_response.json()
    newer_payload = newer_response.json()
    assert older_payload["can_claim_refund"] is False
    assert older_payload["refund_claim_amount_cents"] == 0
    assert newer_payload["can_claim_refund"] is True
    assert newer_payload["refund_claim_amount_cents"] == 500


def test_available_actions_uses_frozen_pwr_quote_for_pwr_refund_projection(client) -> None:
    test_client, _ = client
    order_id = _seed_refundable_order(
        order_id="order-pwr",
        payment_id="payment-pwr",
        settlement_id="settlement-pwr",
        onchain_order_id="99",
        gross_amount_cents=1000,
        platform_fee_cents=30,
        machine_share_cents=270,
        payment_currency="PWR",
        provider_payload={
            "direct_intent_payload": {
                "pwr_amount": str(36 * 10**18),
                "pwr_anchor_price_cents": 25,
            }
        },
    )

    response = test_client.get(f"/api/v1/orders/{order_id}/available-actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_claim_refund"] is True
    assert payload["refund_claim_currency"] == "PWR"
    assert payload["refund_claim_amount_cents"] == 700
    assert payload["refund_claim_amount_pwr"] == 25.2
    assert payload["refund_claim_pwr_anchor_price_cents"] == 25
