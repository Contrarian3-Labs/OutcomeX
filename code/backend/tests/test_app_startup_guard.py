import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


def test_prod_startup_fails_when_onchain_indexer_runtime_is_unavailable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prod-startup.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "prod")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    reset_settings_cache()
    reset_container_cache()

    with pytest.raises(RuntimeError, match="Onchain runtime required in prod"):
        with TestClient(create_app()):
            pass


def test_dev_startup_allows_null_indexer_runtime(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "dev-startup.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200


def test_dev_startup_creates_sqlite_parent_directory(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "nested" / "state" / "app.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert db_path.parent.exists()
    assert db_path.exists()
