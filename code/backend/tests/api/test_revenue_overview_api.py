import os
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.container import get_container, reset_container_cache
from app.core.config import reset_settings_cache
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import (
    Machine,
    MachineRevenueClaim,
    Order,
    Payment,
    RevenueEntry,
    SettlementRecord,
)
from app.main import create_app
from app.onchain.lifecycle_service import get_onchain_lifecycle_service


class StubOnchainLifecycle:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self.user_calls: list[dict[str, str]] = []

    def enabled(self) -> bool:
        return self._enabled

    def send_as_user(self, *, user_id: str, write_result):
        self.user_calls.append(
            {
                "user_id": user_id,
                "method_name": write_result.method_name,
                "contract_name": write_result.contract_name,
            }
        )

        class Broadcast:
            tx_hash = "0xclaimmachinerevenue"

        return Broadcast()


@pytest.fixture
def client(tmp_path) -> TestClient:
    previous_database_url = os.environ.get("OUTCOMEX_DATABASE_URL")
    previous_auto_create_tables = os.environ.get("OUTCOMEX_AUTO_CREATE_TABLES")
    db_path = tmp_path / "revenue-overview-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    if previous_database_url is None:
        os.environ.pop("OUTCOMEX_DATABASE_URL", None)
    else:
        os.environ["OUTCOMEX_DATABASE_URL"] = previous_database_url

    if previous_auto_create_tables is None:
        os.environ.pop("OUTCOMEX_AUTO_CREATE_TABLES", None)
    else:
        os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = previous_auto_create_tables

    reset_settings_cache()
    reset_container_cache()


def _seed_machine_with_revenue(*, owner_user_id: str, machine_share_cents: int) -> Machine:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(
            display_name="Yield Node",
            owner_user_id=owner_user_id,
            onchain_machine_id="51",
            ownership_source="chain",
        )
        db.add(machine)
        db.flush()

        quoted_amount_cents = machine_share_cents + 100
        order = Order(
            user_id=owner_user_id,
            machine_id=machine.id,
            chat_session_id="chat-yield",
            user_prompt="Produce deliverable",
            recommended_plan_summary="Yield test",
            quoted_amount_cents=quoted_amount_cents,
            state=OrderState.RESULT_CONFIRMED,
            settlement_state=SettlementState.DISTRIBUTED,
        )
        db.add(order)
        db.flush()

        payment = Payment(
            order_id=order.id,
            provider="hsp",
            amount_cents=quoted_amount_cents,
            currency="USD",
            state=PaymentState.SUCCEEDED,
        )
        db.add(payment)

        settlement = SettlementRecord(
            order_id=order.id,
            gross_amount_cents=quoted_amount_cents,
            platform_fee_cents=quoted_amount_cents - machine_share_cents,
            machine_share_cents=machine_share_cents,
            state=SettlementState.DISTRIBUTED,
            distributed_at=datetime.now(timezone.utc),
        )
        db.add(settlement)
        db.flush()

        revenue_entry = RevenueEntry(
            order_id=order.id,
            settlement_id=settlement.id,
            machine_id=machine.id,
            beneficiary_user_id=owner_user_id,
            gross_amount_cents=settlement.gross_amount_cents,
            platform_fee_cents=settlement.platform_fee_cents,
            machine_share_cents=machine_share_cents,
            is_self_use=False,
            is_dividend_eligible=True,
        )
        db.add(revenue_entry)
        machine.has_unsettled_revenue = True
        db.add(machine)
        db.commit()
        db.refresh(machine)
        return machine


def _insert_claim(
    *,
    machine_id: str,
    amount_cents: int,
    claimed_at: datetime,
    tx_hash: str,
) -> None:
    container = get_container()
    with container.session_factory() as db:
        claim = MachineRevenueClaim(
            machine_id=machine_id,
            amount_cents=amount_cents,
            tx_hash=tx_hash,
            claimed_at=claimed_at,
        )
        db.add(claim)
        db.commit()


def test_revenue_overview_defaults_to_zero(client: TestClient) -> None:
    owner_user_id = "owner-empty"
    response = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_user_id"] == owner_user_id
    assert payload["paid_cents"] == 0
    assert payload["projected_cents"] == 0
    assert payload["claimable_cents"] == 0
    assert payload["claimed_cents"] == 0
    assert payload["currency"] == "USD"
    assert payload["withdraw_history"] == []


def test_revenue_overview_reports_projected_and_claimed_history(client: TestClient) -> None:
    owner_user_id = "owner-yield"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900)
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    later = now + timedelta(hours=1)
    _insert_claim(machine_id=machine.id, amount_cents=150, claimed_at=now, tx_hash="0xold")
    _insert_claim(machine_id=machine.id, amount_cents=200, claimed_at=later, tx_hash="0xnew")

    response = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paid_cents"] == 1000
    assert payload["projected_cents"] == 900
    assert payload["claimed_cents"] == 350
    assert payload["claimable_cents"] == 550
    assert payload["withdraw_history"][0]["tx_hash"] == "0xnew"
    assert payload["withdraw_history"][1]["tx_hash"] == "0xold"


def test_claim_machine_revenue_persists_withdraw_history(client: TestClient) -> None:
    owner_user_id = "owner-claim"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=800)
    stub = StubOnchainLifecycle(enabled=True)
    client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub

    response = client.post(f"/api/v1/revenue/machines/{machine.id}/claim")
    assert response.status_code == 200
    assert response.json()["tx_hash"] == "0xclaimmachinerevenue"

    overview = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview").json()
    assert overview["claimed_cents"] == 800
    assert overview["claimable_cents"] == 0
    assert overview["withdraw_history"][0]["amount_cents"] == 800
    assert overview["withdraw_history"][0]["tx_hash"] == "0xclaimmachinerevenue"

    client.app.dependency_overrides.pop(get_onchain_lifecycle_service, None)
