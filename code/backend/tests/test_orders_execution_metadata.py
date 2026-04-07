import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "orders-execution-metadata.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
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


def test_order_creation_persists_thin_execution_request(client: TestClient) -> None:
    machine = _create_machine(client)
    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-1",
            "chat_session_id": "chat-1",
            "user_message": "Summarize this process",
        },
    )
    assert plan_response.status_code == 200
    selected_plan = next(
        plan for plan in plan_response.json()["recommended_plans"] if plan["strategy"] == "efficiency"
    )
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Summarize this process",
            "quoted_amount_cents": 1000,
            "selected_plan_id": selected_plan["plan_id"],
            "input_files": ["brief.md", "diagram.png"],
        },
    )
    assert response.status_code == 201

    payload = response.json()
    assert payload["execution_request"] == {
        "intent": "Summarize this process",
        "files": ["brief.md", "diagram.png"],
        "execution_strategy": "efficiency",
    }
    assert payload["execution_metadata"]["gateway"] == "outcomex_agentskillos_thin.v1"
    assert payload["execution_metadata"]["submission_status"] == "draft"
    assert payload["execution_metadata"]["selected_plan_id"] == selected_plan["plan_id"]
    assert payload["execution_metadata"]["selected_plan_strategy"] == "efficiency"
    assert payload["execution_metadata"]["selected_native_plan_index"] == 1

    fetch_response = client.get(f"/api/v1/orders/{payload['id']}")
    assert fetch_response.status_code == 200
    assert fetch_response.json()["execution_request"] == payload["execution_request"]
    assert fetch_response.json()["execution_metadata"] == payload["execution_metadata"]
