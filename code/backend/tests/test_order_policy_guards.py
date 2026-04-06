import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "policy-guards.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str, user_id: str = "user-1", quoted_amount_cents: int = 1000) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": user_id,
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Build me a launch workflow",
            "quoted_amount_cents": quoted_amount_cents,
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_and_confirm_payment(client: TestClient, order_id: str, amount_cents: int, state: str = "succeeded") -> dict:
    intent_response = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": amount_cents, "currency": "usd"},
    )
    assert intent_response.status_code == 201
    payment = intent_response.json()
    confirm_response = client.post(
        f"/api/v1/payments/{payment['payment_id']}/mock-confirm",
        json={"state": state},
    )
    assert confirm_response.status_code == 200
    return payment


def _mark_result_ready(client: TestClient, order_id: str) -> dict:
    response = client.post(f"/api/v1/orders/{order_id}/mock-result-ready")
    assert response.status_code == 200
    return response.json()


def test_order_creation_rejects_unknown_machine(client: TestClient) -> None:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": "missing-machine",
            "chat_session_id": "chat-1",
            "user_prompt": "Need a recommendation",
            "quoted_amount_cents": 1000,
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Machine not found"


def test_payment_and_result_ready_are_required_for_confirm_settlement_and_distribution(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-1", quoted_amount_cents=1000)

    no_payment_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert no_payment_confirm.status_code == 409

    mismatched_intent = client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 600, "currency": "usd"},
    )
    assert mismatched_intent.status_code == 409

    payment = _create_and_confirm_payment(client, order_id=order["id"], amount_cents=1000)
    before_ready_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert before_ready_confirm.status_code == 409

    ready = _mark_result_ready(client, order_id=order["id"])
    assert ready["execution_state"] == "succeeded"
    assert ready["preview_state"] == "ready"

    full_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert full_confirm.status_code == 200
    assert full_confirm.json()["settlement_state"] == "ready"

    settlement_start = client.post(f"/api/v1/settlement/orders/{order['id']}/start")
    assert settlement_start.status_code == 200
    assert settlement_start.json()["state"] == "locked"

    distribution = client.post(f"/api/v1/revenue/orders/{order['id']}/distribute")
    assert distribution.status_code == 200

    payment_fetch = client.get(f"/api/v1/orders/{order['id']}")
    assert payment_fetch.status_code == 200
    assert payment_fetch.json()["settlement_state"] == "distributed"
    assert payment["payment_id"]


def test_mock_confirm_rejects_terminal_state_downgrade(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-1", quoted_amount_cents=1000)
    payment = _create_and_confirm_payment(client, order_id=order["id"], amount_cents=1000)

    downgrade = client.post(
        f"/api/v1/payments/{payment['payment_id']}/mock-confirm",
        json={"state": "failed"},
    )
    assert downgrade.status_code == 409
    assert downgrade.json()["detail"] == "Payment is already in terminal state"


def test_machine_transfer_blocked_until_revenue_distributed(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-2", quoted_amount_cents=500)
    _create_and_confirm_payment(client, order_id=order["id"], amount_cents=500)

    blocked_transfer = client.post(
        f"/api/v1/machines/{machine['id']}/transfer",
        json={"new_owner_user_id": "owner-2", "keep_previous_setup": True},
    )
    assert blocked_transfer.status_code == 409

    _mark_result_ready(client, order_id=order["id"])
    confirm_result = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert confirm_result.status_code == 200

    settlement_start = client.post(f"/api/v1/settlement/orders/{order['id']}/start")
    assert settlement_start.status_code == 200

    distribute = client.post(f"/api/v1/revenue/orders/{order['id']}/distribute")
    assert distribute.status_code == 200

    unblocked_transfer = client.post(
        f"/api/v1/machines/{machine['id']}/transfer",
        json={"new_owner_user_id": "owner-2", "keep_previous_setup": False},
    )
    assert unblocked_transfer.status_code == 200
    assert unblocked_transfer.json()["new_owner_user_id"] == "owner-2"


def test_order_creation_rejects_zero_value_orders(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")

    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Need a recommendation",
            "quoted_amount_cents": 0,
        },
    )
    assert response.status_code == 422


def test_settlement_policy_is_frozen_when_payment_succeeds(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-2", quoted_amount_cents=500)

    _create_and_confirm_payment(client, order_id=order["id"], amount_cents=500)
    order_after_payment = client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_payment.status_code == 200
    payload = order_after_payment.json()
    assert payload["settlement_beneficiary_user_id"] == "owner-1"
    assert payload["settlement_is_self_use"] is False
    assert payload["settlement_is_dividend_eligible"] is True


def test_transfer_remains_blocked_until_all_unsettled_orders_are_distributed(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order_a = _create_order(client, machine_id=machine["id"], user_id="user-a", quoted_amount_cents=500)
    order_b = _create_order(client, machine_id=machine["id"], user_id="user-b", quoted_amount_cents=700)

    _create_and_confirm_payment(client, order_id=order_a["id"], amount_cents=500)
    _create_and_confirm_payment(client, order_id=order_b["id"], amount_cents=700)
    _mark_result_ready(client, order_id=order_a["id"])
    _mark_result_ready(client, order_id=order_b["id"])
    assert client.post(f"/api/v1/orders/{order_a['id']}/confirm-result").status_code == 200
    assert client.post(f"/api/v1/orders/{order_b['id']}/confirm-result").status_code == 200
    assert client.post(f"/api/v1/settlement/orders/{order_a['id']}/start").status_code == 200
    assert client.post(f"/api/v1/settlement/orders/{order_b['id']}/start").status_code == 200

    distribute_a = client.post(f"/api/v1/revenue/orders/{order_a['id']}/distribute")
    assert distribute_a.status_code == 200
    still_blocked_transfer = client.post(
        f"/api/v1/machines/{machine['id']}/transfer",
        json={"new_owner_user_id": "owner-2", "keep_previous_setup": True},
    )
    assert still_blocked_transfer.status_code == 409

    distribute_b = client.post(f"/api/v1/revenue/orders/{order_b['id']}/distribute")
    assert distribute_b.status_code == 200
    unblocked_transfer = client.post(
        f"/api/v1/machines/{machine['id']}/transfer",
        json={"new_owner_user_id": "owner-2", "keep_previous_setup": True},
    )
    assert unblocked_transfer.status_code == 200


def test_confirm_result_rejects_after_settlement_locked(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-1", quoted_amount_cents=1000)
    _create_and_confirm_payment(client, order_id=order["id"], amount_cents=1000)
    _mark_result_ready(client, order_id=order["id"])

    first_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert first_confirm.status_code == 200

    settlement_start = client.post(f"/api/v1/settlement/orders/{order['id']}/start")
    assert settlement_start.status_code == 200

    repeated_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert repeated_confirm.status_code == 409
    assert repeated_confirm.json()["detail"] == "Order result confirmation already finalized for settlement"
