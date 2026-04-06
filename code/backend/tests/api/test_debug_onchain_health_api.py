from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


def test_debug_onchain_health_endpoint_returns_report(tmp_path) -> None:
    db_path = tmp_path / "health.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ENV"] = "test"
    os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/debug/onchain-health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chain_id_expected"] == 133
    assert payload["contracts"]["order_book"]["address"].startswith("0x")
    assert payload["warnings"] == ["rpc_url_missing"]

    reset_settings_cache()
    reset_container_cache()
