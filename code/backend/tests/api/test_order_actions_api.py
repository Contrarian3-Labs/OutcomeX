import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Machine, RevenueEntry, SettlementRecord
from app.main import create_app
from app.onchain.tx_sender import (
    CONFIRM_RESULT_SELECTOR,
    REJECT_VALID_PREVIEW_SELECTOR,
    REFUND_FAILED_OR_NO_VALID_PREVIEW_SELECTOR,
)


class StubBroadcast:
    def __init__(self, *, tx_hash: str) -> None:
        self.tx_hash = tx_hash
        self.receipt = None


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
        return StubBroadcast(tx_hash=f"0x{write_result.method_name.lower()}")


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, StubOnchainLifecycle]:
    db_path = tmp_path / "order-actions-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = "http://127.0.0.1:8545"
    reset_settings_cache()
    reset_container_cache()

    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True)
    app = create_app()
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub
    with TestClient(app) as test_client:
        yield test_client, stub

    os.environ.pop("OUTCOMEX_ONCHAIN_RPC_URL", None)
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={
            "display_name": "Action Node",
            "owner_user_id": owner_user_id,
            "onchain_machine_id": "7",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str, user_id: str = "buyer-1") -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": user_id,
            "machine_id": machine_id,
            "chat_session_id": "chat-actions-1",
            "user_prompt": "Generate a deliverable",
            "quoted_amount_cents": 1000,
        },
    )
    assert response.status_code == 201
    return response.json()


def _confirm_payment(client: TestClient, order_id: str) -> None:
    intent = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": 1000, "currency": "USD"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]
    confirmed = client.post(f"/api/v1/payments/{payment_id}/mock-confirm", json={"state": "succeeded"})
    assert confirmed.status_code == 200


def _anchor_order(order_id: str) -> None:
    with get_container().session_factory() as db:
        order = db.get(__import__("app.domain.models", fromlist=["Order"]).Order, order_id)
        order.onchain_order_id = "42"
        order.create_order_tx_hash = "0xcreatepaid"
        order.create_order_event_id = "OrderCreated:42:0xcreatepaid"
        order.create_order_block_number = 12
        db.add(order)
        db.commit()


def test_mock_result_ready_persists_onchain_preview_ready_tx_hash(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])

    response = test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready", json={"valid_preview": False})

    assert response.status_code == 200
    order_fetch = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_fetch.status_code == 200
    payload = order_fetch.json()
    assert payload["execution_metadata"]["preview_valid"] is False
    assert payload["execution_metadata"]["onchain_preview_ready_tx_hash"] == "0xmarkpreviewready"


def test_available_actions_show_confirm_and_reject_for_valid_preview(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])

    ready = test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready")
    assert ready.status_code == 200

    response = test_client.get(f"/api/v1/orders/{order['id']}/available-actions")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order["id"],
        "preview_valid": True,
        "can_confirm_result": True,
        "can_reject_valid_preview": True,
        "can_refund_failed_or_no_valid_preview": False,
        "can_claim_refund": False,
    }


def test_available_actions_show_refund_for_invalid_preview(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])

    ready = test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready", json={"valid_preview": False})
    assert ready.status_code == 200

    response = test_client.get(f"/api/v1/orders/{order['id']}/available-actions")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order["id"],
        "preview_valid": False,
        "can_confirm_result": False,
        "can_reject_valid_preview": False,
        "can_refund_failed_or_no_valid_preview": True,
        "can_claim_refund": False,
    }



def test_confirm_result_defaults_to_user_sign_call_intent(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready").status_code == 200
    stub.user_calls.clear()

    response = test_client.post(f"/api/v1/orders/{order['id']}/confirm-result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order["id"]
    assert payload["mode"] == "user_sign"
    assert payload["state"] == "result_pending_confirmation"
    assert payload["settlement_state"] == "not_ready"
    assert payload["contract_name"] == "OrderBook"
    assert payload["method_name"] == "confirmResult"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000133"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"order_id": "42"}
    assert payload["calldata"].startswith(CONFIRM_RESULT_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.user_calls == []


def test_reject_valid_preview_creates_local_settlement_projection(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready").status_code == 200

    response = test_client.post(f"/api/v1/orders/{order['id']}/reject-valid-preview?mode=server_broadcast")

    assert response.status_code == 200
    with get_container().session_factory() as db:
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order["id"]).one()
        revenue_entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == order["id"]).one()
        db_machine = db.get(Machine, machine["id"])

        assert settlement.gross_amount_cents == 1000
        assert settlement.platform_fee_cents == 30
        assert settlement.machine_share_cents == 270
        assert revenue_entry.platform_fee_cents == 30
        assert revenue_entry.machine_share_cents == 270
        assert db_machine.has_unsettled_revenue is True


def test_reject_valid_preview_broadcasts_buyer_onchain_tx(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready").status_code == 200

    response = test_client.post(f"/api/v1/orders/{order['id']}/reject-valid-preview?mode=server_broadcast")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order["id"],
        "state": "cancelled",
        "settlement_state": "distributed",
        "tx_hash": "0xrejectvalidpreview",
        "contract_name": "OrderBook",
        "method_name": "rejectValidPreview",
    }
    assert stub.user_calls[-1] == {
        "user_id": "buyer-1",
        "method_name": "rejectValidPreview",
        "contract_name": "OrderBook",
    }


def test_reject_valid_preview_defaults_to_user_sign_call_intent(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready").status_code == 200
    stub.user_calls.clear()

    response = test_client.post(f"/api/v1/orders/{order['id']}/reject-valid-preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order["id"]
    assert payload["mode"] == "user_sign"
    assert payload["state"] == "result_pending_confirmation"
    assert payload["settlement_state"] == "not_ready"
    assert payload["contract_name"] == "OrderBook"
    assert payload["method_name"] == "rejectValidPreview"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000133"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"order_id": "42"}
    assert payload["calldata"].startswith(REJECT_VALID_PREVIEW_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.user_calls == []


def test_refund_failed_or_no_valid_preview_creates_zero_revenue_projection(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert (
        test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready", json={"valid_preview": False}).status_code
        == 200
    )

    response = test_client.post(
        f"/api/v1/orders/{order['id']}/refund-failed-or-no-valid-preview?mode=server_broadcast"
    )

    assert response.status_code == 200
    with get_container().session_factory() as db:
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order["id"]).one()
        revenue_entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == order["id"]).one()
        db_machine = db.get(Machine, machine["id"])

        assert settlement.gross_amount_cents == 1000
        assert settlement.platform_fee_cents == 0
        assert settlement.machine_share_cents == 0
        assert revenue_entry.platform_fee_cents == 0
        assert revenue_entry.machine_share_cents == 0
        assert db_machine.has_unsettled_revenue is False


def test_refund_failed_or_no_valid_preview_broadcasts_onchain_tx(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert (
        test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready", json={"valid_preview": False}).status_code
        == 200
    )

    response = test_client.post(
        f"/api/v1/orders/{order['id']}/refund-failed-or-no-valid-preview?mode=server_broadcast"
    )

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order["id"],
        "state": "cancelled",
        "settlement_state": "distributed",
        "tx_hash": "0xrefundfailedornovalidpreview",
        "contract_name": "OrderBook",
        "method_name": "refundFailedOrNoValidPreview",
    }
    assert stub.user_calls[-1] == {
        "user_id": "buyer-1",
        "method_name": "refundFailedOrNoValidPreview",
        "contract_name": "OrderBook",
    }


def test_refund_failed_or_no_valid_preview_defaults_to_user_sign_call_intent(
    client: tuple[TestClient, StubOnchainLifecycle],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    _confirm_payment(test_client, order["id"])
    _anchor_order(order["id"])
    assert (
        test_client.post(f"/api/v1/orders/{order['id']}/mock-result-ready", json={"valid_preview": False}).status_code
        == 200
    )
    stub.user_calls.clear()

    response = test_client.post(f"/api/v1/orders/{order['id']}/refund-failed-or-no-valid-preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order["id"]
    assert payload["mode"] == "user_sign"
    assert payload["state"] == "result_pending_confirmation"
    assert payload["settlement_state"] == "not_ready"
    assert payload["contract_name"] == "OrderBook"
    assert payload["method_name"] == "refundFailedOrNoValidPreview"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000133"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"order_id": "42"}
    assert payload["calldata"].startswith(REFUND_FAILED_OR_NO_VALID_PREVIEW_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.user_calls == []
