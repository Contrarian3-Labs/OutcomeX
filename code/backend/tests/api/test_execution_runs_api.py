import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.domain.enums import ExecutionRunStatus
from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.main import create_app


class _ExecutionServiceStub:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit_task(
        self,
        *,
        external_order_id: str,
        prompt: str,
        input_files=(),
        execution_strategy=ExecutionStrategy.QUALITY,
    ):
        self.submit_calls += 1
        return type(
            "Snapshot",
            (),
            {
                "run_id": "aso-run-test",
                "external_order_id": external_order_id,
                "status": ExecutionRunStatus.QUEUED,
                "submission_payload": {
                    "intent": prompt,
                    "files": list(input_files),
                    "execution_strategy": execution_strategy.value,
                    "selected_plan_index": 0,
                },
                "selected_plan": {"name": "Quality-First", "description": "Deep validation path", "nodes": [{"id": "n1", "name": "docx"}]},
                "workspace_path": None,
                "run_dir": None,
                "preview_manifest": (),
                "artifact_manifest": (),
                "skills_manifest": (),
                "model_usage_manifest": (),
                "summary_metrics": {},
                "error": None,
                "started_at": None,
                "finished_at": None,
            },
        )()

    def get_run(self, run_id: str):
        return type(
            "Snapshot",
            (),
            {
                "run_id": run_id,
                "external_order_id": "order-1",
                "status": ExecutionRunStatus.SUCCEEDED,
                "submission_payload": {
                    "intent": "Write a report",
                    "files": ["brief.md"],
                    "execution_strategy": "quality",
                    "selected_plan_index": 0,
                },
                "selected_plan": {"name": "Quality-First", "description": "Deep validation path", "nodes": [{"id": "n1", "name": "docx"}]},
                "workspace_path": "/tmp/workspace",
                "run_dir": "/tmp/run-dir",
                "preview_manifest": ({"path": "workspace/preview.png", "type": "image", "role": "final"},),
                "artifact_manifest": ({"path": "workspace/report.docx", "type": "document", "role": "final"},),
                "skills_manifest": ({"skill_id": "docx", "skill_path": "/skills/docx", "status": "selected"},),
                "model_usage_manifest": ({"provider": "agentskillos_internal", "model": "openai/qwen3.6-plus"},),
                "summary_metrics": {"total_input_tokens": 10, "total_output_tokens": 20},
                "error": None,
                "started_at": datetime.now(timezone.utc),
                "finished_at": datetime.now(timezone.utc),
            },
        )()

    def cancel_run(self, run_id: str):
        snapshot = self.get_run(run_id)
        snapshot.status = ExecutionRunStatus.CANCELLED
        return snapshot


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, _ExecutionServiceStub]:
    db_path = tmp_path / "execution-runs.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    stub = _ExecutionServiceStub()
    app.dependency_overrides[get_agentskillos_execution_service] = lambda: stub
    with TestClient(app) as test_client:
        yield test_client, stub
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )
    assert response.status_code == 201
    return response.json()


def _create_paid_order(client: TestClient, machine_id: str) -> dict:
    order = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Write a report",
            "quoted_amount_cents": 1000,
            "input_files": ["brief.md"],
            "execution_strategy": "quality",
        },
    )
    assert order.status_code == 201
    order_id = order.json()["id"]

    payment_intent = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": 1000, "currency": "USD"},
    )
    assert payment_intent.status_code == 201
    payment_id = payment_intent.json()["payment_id"]
    confirm = client.post(f"/api/v1/payments/{payment_id}/mock-confirm", json={"state": "succeeded"})
    assert confirm.status_code == 200
    return order.json()


def test_start_execution_creates_run_and_run_endpoint_returns_snapshot(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200
    assert start.json()["id"] == "aso-run-test"
    assert start.json()["status"] == "queued"
    assert start.json()["selected_plan"]["index"] == 0
    assert start.json()["selected_plan"]["name"] == "Quality-First"
    assert start.json()["selected_plan"]["description"] == "Deep validation path"
    assert start.json()["selected_plan_binding"] == {
        "submission_payload_selected_plan_index": 0,
        "selected_plan_index": 0,
        "is_consistent": True,
    }

    run_response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["status"] == "succeeded"
    assert payload["submission_payload"]["execution_strategy"] == "quality"
    assert payload["submission_payload"]["selected_plan_index"] == 0
    assert payload["selected_plan"]["index"] == 0
    assert payload["selected_plan"]["name"] == "Quality-First"
    assert payload["selected_plan"]["description"] == "Deep validation path"
    assert payload["selected_plan"]["nodes"][0]["name"] == "docx"
    assert payload["selected_plan_binding"] == {
        "submission_payload_selected_plan_index": 0,
        "selected_plan_index": 0,
        "is_consistent": True,
    }
    assert payload["artifact_manifest"][0]["path"] == "workspace/report.docx"
    assert payload["skills_manifest"][0]["skill_id"] == "docx"

    order_fetch = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_fetch.status_code == 200
    assert order_fetch.json()["execution_metadata"]["run_id"] == "aso-run-test"
    assert order_fetch.json()["execution_metadata"]["run_status"] == "succeeded"
    assert order_fetch.json()["state"] == "result_pending_confirmation"

    confirm = test_client.post(f"/api/v1/orders/{order['id']}/confirm-result")
    assert confirm.status_code == 200
    assert confirm.json()["state"] == "result_confirmed"
    assert confirm.json()["settlement_state"] == "ready"

    polled_again = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert polled_again.status_code == 200

    order_after_confirm_poll = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_confirm_poll.status_code == 200
    assert order_after_confirm_poll.json()["state"] == "result_confirmed"
    assert order_after_confirm_poll.json()["settlement_state"] == "ready"


def test_start_execution_rejects_duplicate_active_run(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    first = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert first.status_code == 200
    assert stub.submit_calls == 1

    second = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert second.status_code == 409
    assert second.json()["detail"] == "Execution already in progress for this order"
    assert stub.submit_calls == 1
