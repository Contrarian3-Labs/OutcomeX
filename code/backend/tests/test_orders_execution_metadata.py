import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "orders-execution-metadata.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )
    assert response.status_code == 201
    return response.json()


def test_order_creation_persists_execution_metadata(client: TestClient) -> None:
    machine = _create_machine(client)
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Summarize this process",
            "quoted_amount_cents": 1000,
        },
    )
    assert response.status_code == 201

    payload = response.json()
    assert payload["execution_metadata"]["planner"] == "agentskillos_wrapper.v1"
    assert payload["execution_metadata"]["primary_output"] == "text"
    assert payload["execution_metadata"]["match_status"] == "matched"
    assert payload["execution_metadata"]["selected_model"] == "builtin/text-fast"

    fetch_response = client.get(f"/api/v1/orders/{payload['id']}")
    assert fetch_response.status_code == 200
    assert fetch_response.json()["execution_metadata"] == payload["execution_metadata"]
