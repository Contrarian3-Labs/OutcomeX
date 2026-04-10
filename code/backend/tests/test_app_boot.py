import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings, reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


def test_health_endpoint_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
    reset_settings_cache()
    reset_container_cache()
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    reset_settings_cache()
    reset_container_cache()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "outcomex-backend"}


def test_settings_load_env_file_from_backend_root_even_when_cwd_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OUTCOMEX_DATABASE_URL", raising=False)
    monkeypatch.chdir("/mnt/c/users/72988/desktop/hashkey")
    reset_settings_cache()

    settings = get_settings()

    reset_settings_cache()
    assert settings.database_url == (
        "sqlite+pysqlite:////mnt/c/users/72988/desktop/OutcomeX/code/backend/outcomex-local.db"
    )

