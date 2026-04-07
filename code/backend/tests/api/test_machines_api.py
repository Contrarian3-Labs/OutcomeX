import os

import pytest
from fastapi.testclient import TestClient
from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    previous_onchain_rpc_url = os.environ.get("OUTCOMEX_ONCHAIN_RPC_URL")
    db_path = tmp_path / "machines-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ.pop("OUTCOMEX_ONCHAIN_RPC_URL", None)
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as test_client:
        yield test_client

    if previous_onchain_rpc_url is None:
        os.environ.pop("OUTCOMEX_ONCHAIN_RPC_URL", None)
    else:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = previous_onchain_rpc_url
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient, *, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _get_machine(client: TestClient, machine_id: str) -> dict:
    response = client.get("/api/v1/machines")
    assert response.status_code == 200
    for machine in response.json():
        if machine["id"] == machine_id:
            return machine
    raise AssertionError(f"Machine {machine_id} not found in list response")


def test_create_machine_exposes_bootstrap_ownership_state(client: TestClient) -> None:
    machine = _create_machine(client)

    assert machine["owner_user_id"] == "owner-1"
    assert machine["ownership_source"] == "bootstrap"
    assert machine["pending_transfer_new_owner_user_id"] is None


class StubOnchainLifecycle:
    def __init__(self, *, enabled: bool, onchain_machine_id: str | None = None) -> None:
        self._enabled = enabled
        self._onchain_machine_id = onchain_machine_id
        self.mint_calls: list[dict[str, str]] = []

    def enabled(self) -> bool:
        return self._enabled

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str):
        self.mint_calls.append({"owner_user_id": owner_user_id, "token_uri": token_uri})
        class Receipt:
            def __init__(self, onchain_machine_id: str | None):
                self.onchain_machine_id = onchain_machine_id
        return Receipt(self._onchain_machine_id)


def test_create_machine_mints_onchain_when_runtime_enabled(client: TestClient) -> None:
    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True, onchain_machine_id="101")
    client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub

    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["onchain_machine_id"] == "101"
    assert payload["ownership_source"] == "chain"
    assert stub.mint_calls[0]["owner_user_id"] == "owner-1"


def test_create_machine_skips_mint_when_onchain_machine_id_is_provided(client: TestClient) -> None:
    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True, onchain_machine_id="999")
    client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub

    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1", "onchain_machine_id": "77"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["onchain_machine_id"] == "77"
    assert payload["ownership_source"] == "bootstrap"
    assert stub.mint_calls == []
