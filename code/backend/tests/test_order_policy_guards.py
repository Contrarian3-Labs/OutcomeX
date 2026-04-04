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


def test_payment_is_required_for_confirm_settlement_and_distribution(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-1", quoted_amount_cents=1000)

    no_payment_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert no_payment_confirm.status_code == 409

    payment_a = _create_and_confirm_payment(client, order_id=order["id"], amount_cents=600)
    partial_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert partial_confirm.status_code == 409

    _create_and_confirm_payment(client, order_id=order["id"], amount_cents=400)
    full_confirm = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert full_confirm.status_code == 200
    assert full_confirm.json()["settlement_state"] == "ready"

    downgrade_payment = client.post(
        f"/api/v1/payments/{payment_a['payment_id']}/mock-confirm",
        json={"state": "failed"},
    )
    assert downgrade_payment.status_code == 200

    underpaid_settlement = client.post(f"/api/v1/settlement/orders/{order['id']}/start")
    assert underpaid_settlement.status_code == 409

    restore_payment = client.post(
        f"/api/v1/payments/{payment_a['payment_id']}/mock-confirm",
        json={"state": "succeeded"},
    )
    assert restore_payment.status_code == 200

    settlement_start = client.post(f"/api/v1/settlement/orders/{order['id']}/start")
    assert settlement_start.status_code == 200
    assert settlement_start.json()["state"] == "locked"

    second_downgrade = client.post(
        f"/api/v1/payments/{payment_a['payment_id']}/mock-confirm",
        json={"state": "failed"},
    )
    assert second_downgrade.status_code == 200

    underpaid_distribution = client.post(f"/api/v1/revenue/orders/{order['id']}/distribute")
    assert underpaid_distribution.status_code == 409


def test_machine_transfer_blocked_until_revenue_distributed(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-1")
    order = _create_order(client, machine_id=machine["id"], user_id="user-2", quoted_amount_cents=500)
    _create_and_confirm_payment(client, order_id=order["id"], amount_cents=500)

    confirm_result = client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert confirm_result.status_code == 200

    blocked_transfer = client.post(
        f"/api/v1/machines/{machine['id']}/transfer",
        json={"new_owner_user_id": "owner-2", "keep_previous_setup": True},
    )
    assert blocked_transfer.status_code == 409

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
