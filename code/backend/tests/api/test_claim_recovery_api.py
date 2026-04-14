from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.main import create_app
from app.onchain.claim_state_reader import get_settlement_claim_state_reader
from app.onchain.lifecycle_service import get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriteResult, get_order_writer


class StubClaimStateReader:
    def __init__(self) -> None:
        self.refundable_value = 0
        self.machine_claimable_value = 0
        self.refund_calls: list[tuple[str, str]] = []
        self.machine_calls: list[tuple[str, str]] = []

    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        self.refund_calls.append((user_id, currency))
        return self.refundable_value

    def platform_accrued_amount(self, *, currency: str) -> int:
        return 0

    def machine_claimable_amount(self, *, onchain_machine_id: str, owner_user_id: str) -> int:
        self.machine_calls.append((onchain_machine_id, owner_user_id))
        return self.machine_claimable_value


@dataclass
class StubBroadcast:
    tx_hash: str


class StubLifecycleService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def enabled(self) -> bool:
        return True

    def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> StubBroadcast:
        self.calls.append((user_id, write_result.method_name))
        return StubBroadcast(tx_hash=f"0x{write_result.method_name.lower()}")


class StubOrderWriter:
    def claim_refund(self, *, currency: str, user_id: str, order_id: str) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-refund",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="SettlementController",
            contract_address="0x0000000000000000000000000000000000000135",
            method_name="claimRefund",
            idempotency_key=f"refund:{order_id}",
            payload={"payment_token_address": "0x0000000000000000000000000000000000000a11" if currency == "PWR" else "0x372325443233febaC1F6998aC750276468c83CC6"},
        )

    def claim_machine_revenue(self, machine: Machine) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-machine",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="RevenueVault",
            contract_address="0x0000000000000000000000000000000000000136",
            method_name="claimMachineRevenue",
            idempotency_key=f"machine:{machine.id}:{machine.owner_user_id}",
            payload={"machine_id": int(machine.onchain_machine_id or "0")},
        )

    def confirm_result(self, order: Order) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-confirm",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="confirmResult",
            idempotency_key=f"confirm:{order.id}",
            payload={"order_id": int(order.onchain_order_id or "0")},
        )

    def reject_valid_preview(self, order: Order) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-reject",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="rejectValidPreview",
            idempotency_key=f"reject:{order.id}",
            payload={"order_id": int(order.onchain_order_id or "0")},
        )

    def refund_failed_or_no_valid_preview(self, order: Order) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-failed-refund",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="refundFailedOrNoValidPreview",
            idempotency_key=f"failed-refund:{order.id}",
            payload={"order_id": int(order.onchain_order_id or "0")},
        )

    def mark_preview_ready(self, order: Order, *, valid_preview: bool = True) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xwrite-preview-ready",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="markPreviewReady",
            idempotency_key=f"preview:{order.id}",
            payload={"order_id": int(order.onchain_order_id or "0"), "valid_preview": valid_preview},
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "claim-recovery.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    reset_settings_cache()
    reset_container_cache()

    claim_reader = StubClaimStateReader()
    lifecycle = StubLifecycleService()
    order_writer = StubOrderWriter()

    app = create_app()
    app.dependency_overrides[get_settlement_claim_state_reader] = lambda: claim_reader
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: lifecycle
    app.dependency_overrides[get_order_writer] = lambda: order_writer
    with TestClient(app) as test_client:
        yield test_client, claim_reader, lifecycle

    reset_settings_cache()
    reset_container_cache()


def _seed_refundable_order(*, user_id: str = "buyer", payment_currency: str = "USDT") -> str:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(
            id="machine-1",
            display_name="Node",
            owner_user_id="owner-1",
            onchain_machine_id="51",
        )
        order = Order(
            id="order-1",
            user_id=user_id,
            machine_id=machine.id,
            onchain_order_id="99",
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
            currency=payment_currency,
            state=PaymentState.SUCCEEDED,
        )
        settlement = SettlementRecord(
            id="settlement-1",
            order_id=order.id,
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=0,
            state=SettlementState.DISTRIBUTED,
            distributed_at=datetime.now(timezone.utc),
        )
        db.add_all([machine, order, payment, settlement])
        db.commit()
    return "order-1"


def _seed_claimable_machine(*, owner_user_id: str = "owner-1") -> str:
    container = get_container()
    with container.session_factory() as db:
        machine = Machine(
            id="machine-claim-1",
            display_name="Node Claim",
            owner_user_id=owner_user_id,
            onchain_machine_id="88",
        )
        db.add(machine)
        db.commit()
    return "machine-claim-1"


def test_claim_order_refund_uses_onchain_claim_state_and_broadcasts_for_buyer(client) -> None:
    test_client, claim_reader, lifecycle = client
    order_id = _seed_refundable_order(user_id="buyer", payment_currency="USDT")
    claim_reader.refundable_value = 700_000

    response = test_client.post(f"/api/v1/orders/{order_id}/claim-refund")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order_id
    assert payload["claimant_user_id"] == "buyer"
    assert payload["currency"] == "USDT"
    assert payload["tx_hash"] == "0xclaimrefund"
    assert claim_reader.refund_calls == [("buyer", "USDT")]
    assert lifecycle.calls == [("buyer", "claimRefund")]


def test_claim_machine_revenue_uses_owner_identity_and_user_sign_mode(client) -> None:
    test_client, claim_reader, lifecycle = client
    machine_id = _seed_claimable_machine(owner_user_id="owner-1")
    claim_reader.machine_claimable_value = 5 * 10**18

    response = test_client.post(f"/api/v1/revenue/accounts/owner-1/machines/{machine_id}/claim?mode=user_sign")

    assert response.status_code == 200
    payload = response.json()
    assert payload["machine_id"] == machine_id
    assert payload["onchain_machine_id"] == "88"
    assert payload["claimant_user_id"] == "owner-1"
    assert payload["mode"] == "user_sign"
    assert payload["contract_name"] == "RevenueVault"
    assert payload["method_name"] == "claimMachineRevenue"
    assert payload["calldata"].startswith("0x379607f5")
    assert claim_reader.machine_calls == [("88", "owner-1")]
    assert lifecycle.calls == []


def test_confirm_result_exposes_user_sign_payload_for_recoverable_settlement_flow(client) -> None:
    test_client, _, lifecycle = client
    order_id = _seed_refundable_order(user_id="buyer", payment_currency="USDT")

    container = get_container()
    with container.session_factory() as db:
        order = db.get(Order, order_id)
        order.state = OrderState.RESULT_PENDING_CONFIRMATION
        order.execution_state = ExecutionState.SUCCEEDED
        order.preview_state = PreviewState.READY
        order.settlement_state = SettlementState.READY
        db.add(order)
        db.commit()

    response = test_client.post(f"/api/v1/orders/{order_id}/confirm-result?mode=user_sign")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order_id
    assert payload["mode"] == "user_sign"
    assert payload["contract_name"] == "OrderBook"
    assert payload["method_name"] == "confirmResult"
    assert payload["calldata"].startswith("0xeb05cf51")
    assert lifecycle.calls == []


def test_confirm_result_server_broadcast_projects_local_confirmed_state(client) -> None:
    test_client, _, lifecycle = client
    order_id = _seed_refundable_order(user_id="buyer", payment_currency="USDT")

    container = get_container()
    with container.session_factory() as db:
        order = db.get(Order, order_id)
        order.state = OrderState.RESULT_PENDING_CONFIRMATION
        order.execution_state = ExecutionState.SUCCEEDED
        order.preview_state = PreviewState.READY
        order.settlement_state = SettlementState.READY
        db.add(order)
        db.commit()

    response = test_client.post(f"/api/v1/orders/{order_id}/confirm-result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order_id
    assert payload["state"] == "result_confirmed"
    assert payload["settlement_state"] == "distributed"
    assert payload["tx_hash"] == "0xconfirmresult"
    assert lifecycle.calls == [("buyer", "confirmResult")]

    with container.session_factory() as db:
        order = db.get(Order, order_id)
        entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == order_id).one()
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order_id).one()
        assert order is not None
        assert order.state == OrderState.RESULT_CONFIRMED
        assert order.settlement_state == SettlementState.DISTRIBUTED
        assert order.result_confirmed_at is not None
        assert settlement.state == SettlementState.DISTRIBUTED
        assert entry.beneficiary_user_id == "owner-1"
        assert entry.machine_share_cents == 900


def test_mock_result_ready_projects_authoritative_preview_state(client) -> None:
    test_client, _, lifecycle = client
    order_id = _seed_refundable_order(user_id="buyer", payment_currency="USDT")

    container = get_container()
    with container.session_factory() as db:
        order = db.get(Order, order_id)
        order.state = OrderState.EXECUTING
        order.execution_state = ExecutionState.RUNNING
        order.preview_state = PreviewState.GENERATING
        order.settlement_state = SettlementState.READY
        db.add(order)
        db.commit()

    response = test_client.post(f"/api/v1/orders/{order_id}/mock-result-ready", json={"valid_preview": True})

    assert response.status_code == 200
    assert response.json()["state"] == "result_pending_confirmation"

    with container.session_factory() as db:
        order = db.get(Order, order_id)
        assert order is not None
        assert order.state == OrderState.RESULT_PENDING_CONFIRMATION
        assert order.preview_state == PreviewState.READY
        assert order.execution_state == ExecutionState.SUCCEEDED
        metadata = dict(order.execution_metadata or {})
        assert metadata["preview_valid"] is True
        assert metadata["authoritative_order_status"] == "PREVIEW_READY"
        assert metadata["onchain_preview_ready_tx_hash"] == "0xmarkpreviewready"
    assert lifecycle.calls == [("owner-1", "markPreviewReady")]
