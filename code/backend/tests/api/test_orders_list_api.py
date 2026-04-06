import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import OrderState
from app.domain.models import Order
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "orders-list-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient, owner_user_id: str) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": f"Node {owner_user_id}", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(
    client: TestClient,
    *,
    user_id: str,
    machine_id: str,
    chat_session_id: str,
    user_prompt: str,
) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": user_id,
            "machine_id": machine_id,
            "chat_session_id": chat_session_id,
            "user_prompt": user_prompt,
            "quoted_amount_cents": 1000,
        },
    )
    assert response.status_code == 201
    return response.json()


def _set_order_state(order_id: str, state: OrderState) -> None:
    with get_container().session_factory() as db:
        order = db.get(Order, order_id)
        assert order is not None
        order.state = state
        db.add(order)
        db.commit()


def _sort_key(order_payload: dict) -> tuple[datetime, str]:
    return (datetime.fromisoformat(order_payload["created_at"]), order_payload["id"])


def test_orders_list_default_desc_order(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-list-1")
    _create_order(
        client,
        user_id="user-list-1",
        machine_id=machine["id"],
        chat_session_id="chat-default-1",
        user_prompt="first",
    )
    _create_order(
        client,
        user_id="user-list-1",
        machine_id=machine["id"],
        chat_session_id="chat-default-2",
        user_prompt="second",
    )
    _create_order(
        client,
        user_id="user-list-1",
        machine_id=machine["id"],
        chat_session_id="chat-default-3",
        user_prompt="third",
    )

    response = client.get("/api/v1/orders", params={"user_id": "user-list-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert len(payload["items"]) == 3
    keys = [_sort_key(item) for item in payload["items"]]
    assert keys == sorted(keys, reverse=True)


def test_orders_list_supports_state_filter(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-list-2")
    order_default = _create_order(
        client,
        user_id="user-list-2",
        machine_id=machine["id"],
        chat_session_id="chat-state-1",
        user_prompt="default state",
    )
    order_executing = _create_order(
        client,
        user_id="user-list-2",
        machine_id=machine["id"],
        chat_session_id="chat-state-2",
        user_prompt="executing state",
    )

    _set_order_state(order_executing["id"], OrderState.EXECUTING)
    _set_order_state(order_default["id"], OrderState.CANCELLED)

    response = client.get(
        "/api/v1/orders",
        params={"user_id": "user-list-2", "state": OrderState.EXECUTING.value},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert [item["id"] for item in payload["items"]] == [order_executing["id"]]
    assert payload["items"][0]["state"] == OrderState.EXECUTING.value


def test_orders_list_cursor_pagination(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-list-3")
    for index in range(5):
        _create_order(
            client,
            user_id="user-list-3",
            machine_id=machine["id"],
            chat_session_id=f"chat-cursor-{index}",
            user_prompt=f"cursor order {index}",
        )

    full = client.get("/api/v1/orders", params={"user_id": "user-list-3", "limit": 10})
    assert full.status_code == 200
    expected_ids = [item["id"] for item in full.json()["items"]]
    assert len(expected_ids) == 5

    page1 = client.get("/api/v1/orders", params={"user_id": "user-list-3", "limit": 2})
    assert page1.status_code == 200
    payload1 = page1.json()
    assert len(payload1["items"]) == 2
    assert payload1["next_cursor"] is not None

    page2 = client.get(
        "/api/v1/orders",
        params={
            "user_id": "user-list-3",
            "limit": 2,
            "cursor": payload1["next_cursor"],
        },
    )
    assert page2.status_code == 200
    payload2 = page2.json()
    assert len(payload2["items"]) == 2
    assert payload2["next_cursor"] is not None

    page3 = client.get(
        "/api/v1/orders",
        params={
            "user_id": "user-list-3",
            "limit": 2,
            "cursor": payload2["next_cursor"],
        },
    )
    assert page3.status_code == 200
    payload3 = page3.json()
    assert len(payload3["items"]) == 1
    assert payload3["next_cursor"] is None

    collected_ids = (
        [item["id"] for item in payload1["items"]]
        + [item["id"] for item in payload2["items"]]
        + [item["id"] for item in payload3["items"]]
    )
    assert collected_ids == expected_ids


def test_orders_list_isolated_by_user_id(client: TestClient) -> None:
    machine = _create_machine(client, owner_user_id="owner-list-4")
    user_1_order = _create_order(
        client,
        user_id="user-list-4-a",
        machine_id=machine["id"],
        chat_session_id="chat-user-a",
        user_prompt="for user a",
    )
    _create_order(
        client,
        user_id="user-list-4-b",
        machine_id=machine["id"],
        chat_session_id="chat-user-b",
        user_prompt="for user b",
    )

    response = client.get("/api/v1/orders", params={"user_id": "user-list-4-a"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert [item["id"] for item in payload["items"]] == [user_1_order["id"]]
    assert all(item["user_id"] == "user-list-4-a" for item in payload["items"])
