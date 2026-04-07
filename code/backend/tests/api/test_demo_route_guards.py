from fastapi.testclient import TestClient
import pytest

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


def _non_dev_test_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "demo-guards.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "stage")
    monkeypatch.setenv("OUTCOMEX_EXECUTION_SYNC_ENABLED", "false")
    reset_settings_cache()
    reset_container_cache()
    return TestClient(create_app())


def test_mock_confirm_payment_is_forbidden_outside_dev_test(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _non_dev_test_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/payments/payment-1/mock-confirm", json={"state": "succeeded"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Mock payment confirmation is only available in dev/test"


def test_mock_result_ready_is_forbidden_outside_dev_test(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _non_dev_test_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/orders/order-1/mock-result-ready")

    assert response.status_code == 403
    assert response.json()["detail"] == "Mock result-ready is only available in dev/test"


def test_debug_router_is_not_mounted_outside_dev_test(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _non_dev_test_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/debug/smoke-reset")

    assert response.status_code == 404
