import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Machine
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "order-routing.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
    monkeypatch.setenv("OUTCOMEX_DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("OUTCOMEX_AGENTSKILLOS_ROOT", "")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_INDEXER_ENABLED", "false")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    reset_settings_cache()
    reset_container_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _create_machine(
    client: TestClient,
    *,
    owner_user_id: str,
    display_name: str,
) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": display_name, "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, *, user_id: str, machine_id: str | None = None) -> TestClient:
    payload = {
        "user_id": user_id,
        "chat_session_id": "chat-route-1",
        "user_prompt": "Create a launch-ready AI outcome",
        "quoted_amount_cents": 1000,
    }
    if machine_id is not None:
        payload["machine_id"] = machine_id
    return client.post("/api/v1/orders", json=payload)


def _mark_machine_unavailable(machine_id: str) -> None:
    with get_container().session_factory() as db:
        machine = db.get(Machine, machine_id)
        assert machine is not None
        machine.has_active_tasks = True
        db.add(machine)
        db.commit()


def test_create_order_auto_routes_to_first_available_non_owner_machine(client: TestClient) -> None:
    own_machine = _create_machine(client, owner_user_id="buyer-1", display_name="Own machine")
    locked_machine = _create_machine(client, owner_user_id="owner-locked", display_name="Locked machine")
    available_machine = _create_machine(client, owner_user_id="owner-available", display_name="Available machine")
    _mark_machine_unavailable(locked_machine["id"])

    response = _create_order(client, user_id="buyer-1")

    assert response.status_code == 201
    payload = response.json()
    assert payload["machine_id"] == available_machine["id"]
    assert payload["machine_id"] != own_machine["id"]


def test_create_order_rejects_targeting_own_machine(client: TestClient) -> None:
    own_machine = _create_machine(client, owner_user_id="buyer-1", display_name="Own machine")
    _create_machine(client, owner_user_id="owner-2", display_name="Shared machine")

    response = _create_order(client, user_id="buyer-1", machine_id=own_machine["id"])

    assert response.status_code == 409
    assert response.json()["detail"] == "Create order cannot target your own machine. Use the self-use workspace instead."


def test_create_order_errors_when_no_available_non_owner_machine_exists(client: TestClient) -> None:
    _create_machine(client, owner_user_id="buyer-1", display_name="Own machine")

    response = _create_order(client, user_id="buyer-1")

    assert response.status_code == 409
    assert response.json()["detail"] == "No active non-owner machine is currently available for routed execution."
