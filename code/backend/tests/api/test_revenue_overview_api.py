import os
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.container import get_container, reset_container_cache
from app.core.config import reset_settings_cache
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import (
    Machine,
    Order,
    Payment,
    RevenueEntry,
    SettlementClaimRecord,
    SettlementRecord,
)
from app.main import create_app


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


def _seed_machine_with_revenue(*, owner_user_id: str, machine_share_cents: int, payment_currency: str = "USD") -> Machine:
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
            currency=payment_currency,
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
    claimant_user_id: str,
    machine_id: str,
    amount_cents: int,
    claimed_at: datetime,
    tx_hash: str,
) -> None:
    container = get_container()
    with container.session_factory() as db:
        claim = SettlementClaimRecord(
            event_id=f"evt-{tx_hash}",
            claim_kind="machine_revenue",
            claimant_user_id=claimant_user_id,
            account_address="0xclaimant",
            token_address="0x0000000000000000000000000000000000000a11",
            amount_cents=amount_cents,
            tx_hash=tx_hash,
            machine_id=machine_id,
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
    assert payload["pwr_anchor_price_cents"] is None
    assert payload["withdraw_history"] == []


def test_revenue_overview_reports_projected_and_claimed_history(client: TestClient) -> None:
    owner_user_id = "owner-yield"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900)
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    later = now + timedelta(hours=1)
    _insert_claim(claimant_user_id=owner_user_id, machine_id=machine.id, amount_cents=150, claimed_at=now, tx_hash="0xold")
    _insert_claim(claimant_user_id=owner_user_id, machine_id=machine.id, amount_cents=200, claimed_at=later, tx_hash="0xnew")

    response = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paid_cents"] == 1000
    assert payload["currency"] == "PWR"
    assert payload["pwr_anchor_price_cents"] == 25
    assert payload["projected_cents"] == 900
    assert payload["claimed_cents"] == 350
    assert payload["claimable_cents"] == 550
    assert payload["projected_pwr"] == 36.0
    assert payload["claimed_pwr"] == 14.0
    assert payload["claimable_pwr"] == 22.0
    assert payload["withdraw_history"][0]["amount_pwr"] == 8.0
    assert payload["withdraw_history"][1]["amount_pwr"] == 6.0
    assert payload["withdraw_history"][0]["tx_hash"] == "0xnew"
    assert payload["withdraw_history"][1]["tx_hash"] == "0xold"


def test_revenue_analytics_reports_windows_breakdown_and_apr(client: TestClient) -> None:
    owner_user_id = "owner-analytics"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900)

    container = get_container()
    now = datetime.now(timezone.utc)
    with container.session_factory() as db:
        first_entry = db.scalar(select(RevenueEntry).where(RevenueEntry.machine_id == machine.id))
        assert first_entry is not None
        first_entry.created_at = now - timedelta(days=2)
        db.add(first_entry)

        second_order = Order(
            user_id=owner_user_id,
            machine_id=machine.id,
            chat_session_id="chat-yield-analytics-2",
            user_prompt="Second delivery",
            recommended_plan_summary="Yield analytics",
            quoted_amount_cents=600,
            state=OrderState.RESULT_CONFIRMED,
            settlement_state=SettlementState.DISTRIBUTED,
        )
        db.add(second_order)
        db.flush()
        second_settlement = SettlementRecord(
            order_id=second_order.id,
            gross_amount_cents=600,
            platform_fee_cents=60,
            machine_share_cents=540,
            state=SettlementState.DISTRIBUTED,
            distributed_at=now - timedelta(days=1),
        )
        db.add(second_settlement)
        db.flush()
        db.add(
            RevenueEntry(
                order_id=second_order.id,
                settlement_id=second_settlement.id,
                machine_id=machine.id,
                beneficiary_user_id=owner_user_id,
                gross_amount_cents=600,
                platform_fee_cents=60,
                machine_share_cents=540,
                is_self_use=False,
                is_dividend_eligible=True,
                created_at=now - timedelta(days=1),
            )
        )
        db.commit()

    response = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_user_id"] == owner_user_id
    assert payload["currency"] == "PWR"
    assert payload["total_earned_cents"] == 1440
    assert payload["last_7d_cents"] == 1440
    assert payload["trailing_30d_cents"] == 1440
    assert payload["total_earned_pwr"] == 57.6
    assert payload["last_7d_pwr"] == 57.6
    assert payload["trailing_30d_pwr"] == 57.6
    assert payload["acquisition_total_cents"] == 399900
    assert payload["indicative_apr"] > 0
    assert len(payload["series_7d"]) == 7
    assert len(payload["series_30d"]) == 30
    assert len(payload["series_90d"]) == 90
    assert len(payload["machine_breakdown"]) == 1
    assert payload["machine_breakdown"][0]["machine_id"] == machine.id
    assert payload["machine_breakdown"][0]["total_earned_cents"] == 1440
    assert payload["machine_breakdown"][0]["total_earned_pwr"] == 57.6
    assert payload["machine_breakdown"][0]["claimable_pwr"] == 57.6
    assert payload["machine_breakdown"][0]["acquisition_price_cents"] == 399900


def test_revenue_overview_stays_with_beneficiary_after_machine_owner_changes(client: TestClient) -> None:
    owner_user_id = "owner-before-transfer"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900)
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)

    container = get_container()
    with container.session_factory() as db:
        db_machine = db.get(Machine, machine.id)
        db_machine.owner_user_id = "owner-after-transfer"
        db.add(db_machine)
        db.add(
            SettlementClaimRecord(
                event_id="evt-machine-claim-1",
                claim_kind="machine_revenue",
                claimant_user_id=owner_user_id,
                account_address="0xownerbefore",
                token_address="0x0000000000000000000000000000000000000a11",
                amount_cents=300,
                tx_hash="0xbeneficiary-claim",
                machine_id=machine.id,
                claimed_at=now,
            )
        )
        db.commit()

    original_owner = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview")
    new_owner = client.get("/api/v1/revenue/accounts/owner-after-transfer/overview")

    assert original_owner.status_code == 200
    assert new_owner.status_code == 200
    original_payload = original_owner.json()
    new_owner_payload = new_owner.json()
    assert original_payload["projected_cents"] == 900
    assert original_payload["claimed_cents"] == 300
    assert original_payload["claimable_cents"] == 600
    assert original_payload["pwr_anchor_price_cents"] == 25
    assert original_payload["withdraw_history"][0]["tx_hash"] == "0xbeneficiary-claim"
    assert new_owner_payload["projected_cents"] == 0
    assert new_owner_payload["claimed_cents"] == 0
    assert new_owner_payload["claimable_cents"] == 0
    assert new_owner_payload["pwr_anchor_price_cents"] is None


def test_payment_ledger_lists_payments_for_user_descending(client: TestClient) -> None:
    owner_user_id = "owner-ledger"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900, payment_currency="USDT")
    earlier = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 1, 11, 30, tzinfo=timezone.utc)

    container = get_container()
    with container.session_factory() as db:
        original_order = db.scalar(select(Order).where(Order.machine_id == machine.id, Order.user_id == owner_user_id))
        assert original_order is not None
        original_order_id = original_order.id
        original_payment = db.scalar(select(Payment).where(Payment.order_id == original_order.id))
        assert original_payment is not None
        original_payment.created_at = earlier
        original_payment.callback_tx_hash = "0xolder"
        db.add(original_payment)

        second_order = Order(
            user_id=owner_user_id,
            machine_id=machine.id,
            chat_session_id="chat-ledger-2",
            user_prompt="Generate launch video",
            recommended_plan_summary="Video plan",
            quoted_amount_cents=2500,
            state=OrderState.USER_CONFIRMED,
            settlement_state=SettlementState.NOT_READY,
        )
        db.add(second_order)
        db.flush()
        db.add(
            Payment(
                order_id=second_order.id,
                provider="direct",
                provider_reference="payment-ref-2",
                amount_cents=2500,
                currency="USDC",
                state=PaymentState.PENDING,
                callback_tx_hash="0xnewer",
                created_at=later,
            )
        )
        second_order_id = second_order.id
        db.commit()

    response = client.get(f"/api/v1/revenue/accounts/{owner_user_id}/payment-ledger")

    assert response.status_code == 200
    payload = response.json()
    assert [item["order_id"] for item in payload] == [second_order_id, original_order_id]
    assert payload[0]["payment_id"]
    assert payload[0]["user_prompt"] == "Generate launch video"
    assert payload[0]["provider"] == "direct"
    assert payload[0]["currency"] == "USDC"
    assert payload[0]["amount_cents"] == 2500
    assert payload[0]["state"] == "pending"
    assert payload[0]["tx_hash"] == "0xnewer"
    assert payload[1]["user_prompt"] == "Produce deliverable"
    assert payload[1]["state"] == "succeeded"
    assert payload[1]["tx_hash"] == "0xolder"


def test_list_machine_revenue_projects_claimed_and_claimable_per_entry_fifo(client: TestClient) -> None:
    owner_user_id = "owner-machine-fifo"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=400)
    container = get_container()
    with container.session_factory() as db:
        second_order = Order(
            user_id=owner_user_id,
            machine_id=machine.id,
            chat_session_id="chat-yield-2",
            user_prompt="Produce another deliverable",
            recommended_plan_summary="Yield test 2",
            quoted_amount_cents=500,
            state=OrderState.RESULT_CONFIRMED,
            settlement_state=SettlementState.DISTRIBUTED,
        )
        db.add(second_order)
        db.flush()
        second_settlement = SettlementRecord(
            order_id=second_order.id,
            gross_amount_cents=500,
            platform_fee_cents=100,
            machine_share_cents=300,
            state=SettlementState.DISTRIBUTED,
            distributed_at=datetime.now(timezone.utc),
        )
        db.add(second_settlement)
        db.flush()
        db.add(
            RevenueEntry(
                order_id=second_order.id,
                settlement_id=second_settlement.id,
                machine_id=machine.id,
                beneficiary_user_id=owner_user_id,
                gross_amount_cents=500,
                platform_fee_cents=100,
                machine_share_cents=300,
                is_self_use=False,
                is_dividend_eligible=True,
            )
        )
        db.add(
            SettlementClaimRecord(
                event_id="evt-machine-fifo-claim",
                claim_kind="machine_revenue",
                claimant_user_id=owner_user_id,
                account_address="0xownerfifo",
                token_address="0x0000000000000000000000000000000000000A11",
                amount_cents=450,
                tx_hash="0xfifo-claim",
                machine_id=machine.id,
                claimed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get(f"/api/v1/revenue/machines/{machine.id}")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    older_entry = payload[1]
    newer_entry = payload[0]
    assert older_entry["machine_share_cents"] == 400
    assert older_entry["claimed_cents"] == 400
    assert older_entry["claimable_cents"] == 0
    assert newer_entry["machine_share_cents"] == 300
    assert newer_entry["claimed_cents"] == 50
    assert newer_entry["claimable_cents"] == 250


def test_list_machine_revenue_maps_null_token_machine_claim_to_pwr_fields(client: TestClient) -> None:
    owner_user_id = "owner-machine-pwr-null-token"
    machine = _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=126)
    container = get_container()
    with container.session_factory() as db:
        db.add(
            SettlementClaimRecord(
                event_id="evt-machine-null-token-pwr",
                claim_kind="machine_revenue",
                claimant_user_id=owner_user_id,
                account_address="0xownerpwr",
                token_address=None,
                amount_cents=126,
                amount_wei="5040000000000000000",
                tx_hash="0xmachine-null-token-pwr",
                machine_id=machine.id,
                claimed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get(f"/api/v1/revenue/machines/{machine.id}")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["claimed_cents"] == 126
    assert payload[0]["claimable_cents"] == 0
    assert payload[0]["machine_share_pwr"] == 5.04
    assert payload[0]["claimed_pwr"] == 5.04
    assert payload[0]["claimable_pwr"] == 0.0


def test_platform_overview_reports_projected_claimed_and_claimable_by_currency(client: TestClient) -> None:
    owner_user_id = "owner-platform-test"
    _seed_machine_with_revenue(owner_user_id=owner_user_id, machine_share_cents=900, payment_currency="USDC")
    container = get_container()
    with container.session_factory() as db:
        db.add(
            SettlementClaimRecord(
                event_id="evt-platform-claim-usdc",
                claim_kind="platform_revenue",
                claimant_user_id="platform",
                account_address="0xtreasury",
                token_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                amount_cents=40,
                tx_hash="0xplatformclaim",
                machine_id=None,
                claimed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get("/api/v1/revenue/platform/overview", params={"currency": "USDC"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["currency"] == "USDC"
    assert payload["projected_cents"] == 100
    assert payload["claimed_cents"] == 40
    assert payload["claimable_cents"] == 60
    assert payload["claim_history"][0]["claim_kind"] == "platform_revenue"
    assert payload["claim_history"][0]["amount_cents"] == 40

