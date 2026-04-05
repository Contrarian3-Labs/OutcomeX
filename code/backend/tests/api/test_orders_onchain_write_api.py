import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.integrations.onchain_broadcaster import get_onchain_broadcaster
from app.main import create_app
from app.onchain.order_writer import get_order_writer


class SpyOrderWriter:
    def __init__(self) -> None:
        self.create_calls: list[str] = []

    def create_order(self, order):
        self.create_calls.append(order.id)
        return None


class SpyOnchainBroadcaster:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def broadcast_create_order(self, *, order, write_result):
        self.calls.append({"order_id": order.id})
        return None


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster]:
    db_path = tmp_path / "orders-onchain-write.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    spy_writer = SpyOrderWriter()
    spy_broadcaster = SpyOnchainBroadcaster()
    app = create_app()
    app.dependency_overrides[get_order_writer] = lambda: spy_writer
    app.dependency_overrides[get_onchain_broadcaster] = lambda: spy_broadcaster
    with TestClient(app) as test_client:
        yield test_client, spy_writer, spy_broadcaster
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )
    assert response.status_code == 201
    return response.json()


def test_create_order_persists_control_plane_order_without_upfront_onchain_writes(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster],
) -> None:
    test_client, spy_writer, spy_broadcaster = client
    machine = _create_machine(test_client)

    response = test_client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Generate a landing page hero",
            "quoted_amount_cents": 1000,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["onchain_order_id"] is None
    assert payload["create_order_tx_hash"] is None
    assert payload["create_order_event_id"] is None
    assert payload["create_order_block_number"] is None
    assert spy_writer.create_calls == []
    assert spy_broadcaster.calls == []

    persisted = test_client.get(f"/api/v1/orders/{payload['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["onchain_order_id"] is None
    assert persisted.json()["create_order_tx_hash"] is None
