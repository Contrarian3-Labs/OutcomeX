import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionRunStatus
from app.execution.contracts import ExecutionStrategy
from app.indexer.execution_sync import sync_execution_runs_once
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.main import create_app


class _ExecutionServiceStub:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []

    def submit_task(
        self,
        *,
        external_order_id: str,
        prompt: str,
        input_files=(),
        execution_strategy=ExecutionStrategy.QUALITY,
        selected_plan_index: int | None = None,
    ):
        self.submit_calls.append(
            {
                "external_order_id": external_order_id,
                "prompt": prompt,
                "input_files": list(input_files),
                "execution_strategy": execution_strategy.value,
                "selected_plan_index": selected_plan_index,
            }
        )
        return type(
            "Snapshot",
            (),
            {
                "run_id": "self-use-run-test",
                "external_order_id": external_order_id,
                "status": ExecutionRunStatus.QUEUED,
                "submission_payload": {
                    "intent": prompt,
                    "files": list(input_files),
                    "execution_strategy": execution_strategy.value,
                    "selected_plan_index": selected_plan_index,
                },
                "selected_plan": {
                    "index": selected_plan_index,
                    "name": "Selected Native Plan",
                    "description": "Self-use selected plan",
                    "nodes": [{"id": "n1", "name": "preview"}],
                },
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
                "pid": 1234,
                "pid_alive": True,
                "stdout_log_path": "/tmp/stdout.log",
                "stderr_log_path": "/tmp/stderr.log",
                "events_log_path": "/tmp/events.ndjson",
                "last_heartbeat_at": datetime.now(timezone.utc),
                "current_phase": "queued",
                "current_step": None,
            },
        )()

    def get_run(self, run_id: str):
        return type(
            "Snapshot",
            (),
            {
                "run_id": run_id,
                "external_order_id": "self-use-read",
                "status": ExecutionRunStatus.SUCCEEDED,
                "submission_payload": {
                    "intent": "Build owner dashboard",
                    "files": ["owner-notes.md"],
                    "execution_strategy": "simplicity",
                    "selected_plan_index": 2,
                },
                "selected_plan": {
                    "index": 2,
                    "name": "Selected Native Plan",
                    "description": "Self-use selected plan",
                    "nodes": [{"id": "n1", "name": "preview"}],
                },
                "workspace_path": "/tmp/workspace",
                "run_dir": "/tmp/run-dir",
                "preview_manifest": ({"path": "workspace/preview.png", "type": "image", "role": "final"},),
                "artifact_manifest": ({"path": "workspace/result.md", "type": "document", "role": "final"},),
                "skills_manifest": ({"skill_id": "preview", "skill_path": "/skills/preview", "status": "selected"},),
                "model_usage_manifest": ({"provider": "agentskillos_internal", "model": "stub-model"},),
                "summary_metrics": {"total_input_tokens": 10, "total_output_tokens": 20},
                "error": None,
                "started_at": datetime.now(timezone.utc),
                "finished_at": datetime.now(timezone.utc),
                "pid": 1234,
                "pid_alive": True,
                "stdout_log_path": "/tmp/stdout.log",
                "stderr_log_path": "/tmp/stderr.log",
                "events_log_path": "/tmp/events.ndjson",
                "last_heartbeat_at": datetime.now(timezone.utc),
                "current_phase": "finished",
                "current_step": "preview",
            },
        )()


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, _ExecutionServiceStub]:
    db_path = tmp_path / "self-use.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_EXECUTION_SYNC_ENABLED"] = "false"
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


def test_self_use_plans_forbid_non_owner(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)

    response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_user_id": "not-owner",
            "machine_id": machine["id"],
            "prompt": "Create a private diagnostics report",
            "execution_strategy": "quality",
        },
    )

    assert response.status_code == 403


def test_self_use_owner_flow_without_order_side_effects(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    owner_user_id = "owner-1"

    plans_response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_user_id": owner_user_id,
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard",
            "execution_strategy": "efficiency",
            "input_files": ["owner-notes.md"],
        },
    )
    assert plans_response.status_code == 200
    plans_payload = plans_response.json()
    assert len(plans_payload["recommended_plans"]) == 3
    assert [plan["strategy"] for plan in plans_payload["recommended_plans"]] == [
        "efficiency",
        "quality",
        "simplicity",
    ]

    before_orders = test_client.get("/api/v1/orders", params={"user_id": owner_user_id})
    assert before_orders.status_code == 200
    assert before_orders.json()["items"] == []

    create_run = test_client.post(
        "/api/v1/self-use/runs",
        json={
            "viewer_user_id": owner_user_id,
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard",
            "execution_strategy": "simplicity",
            "input_files": ["owner-notes.md"],
        },
    )
    assert create_run.status_code == 201
    run_payload = create_run.json()
    assert run_payload["id"] == "self-use-run-test"
    assert run_payload["run_kind"] == "self_use"
    assert run_payload["order_id"] is None
    assert run_payload["machine_id"] == machine["id"]
    assert run_payload["viewer_user_id"] == owner_user_id
    assert run_payload["external_order_id"] == f"self-use:{machine['id']}:{owner_user_id}"
    assert run_payload["submission_payload"]["selected_plan_index"] == 2
    assert stub.submit_calls[0]["selected_plan_index"] == 2

    after_orders = test_client.get("/api/v1/orders", params={"user_id": owner_user_id})
    assert after_orders.status_code == 200
    assert after_orders.json()["items"] == []

    read_run = test_client.get(
        f"/api/v1/self-use/runs/{run_payload['id']}",
        params={"viewer_user_id": owner_user_id},
    )
    assert read_run.status_code == 200
    read_payload = read_run.json()
    assert read_payload["id"] == run_payload["id"]
    assert read_payload["run_kind"] == "self_use"
    assert read_payload["order_id"] is None
    assert read_payload["machine_id"] == machine["id"]
    assert read_payload["viewer_user_id"] == owner_user_id

    forbidden_read = test_client.get(
        f"/api/v1/self-use/runs/{run_payload['id']}",
        params={"viewer_user_id": "not-owner"},
    )
    assert forbidden_read.status_code == 403

    generic_read = test_client.get(f"/api/v1/execution-runs/{run_payload['id']}")
    assert generic_read.status_code == 404

    sync_outcome = sync_execution_runs_once(
        session_factory=get_container().session_factory,
        execution_service=stub,
    )
    assert sync_outcome.scanned_runs == 1
    assert sync_outcome.terminal_runs == 1

    machine_after_sync = test_client.get("/api/v1/machines")
    assert machine_after_sync.status_code == 200
    machine_after_sync_payload = next(item for item in machine_after_sync.json() if item["id"] == machine["id"])
    assert machine_after_sync_payload["has_active_tasks"] is False


def test_self_use_rejects_native_plan_index_mismatch(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)
    plans_response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_user_id": "owner-1",
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard",
            "execution_strategy": "simplicity",
            "input_files": ["owner-notes.md"],
        },
    )
    assert plans_response.status_code == 200
    selected_plan_id = plans_response.json()["recommended_plans"][0]["plan_id"]

    response = test_client.post(
        "/api/v1/self-use/runs",
        json={
            "viewer_user_id": "owner-1",
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard",
            "execution_strategy": "simplicity",
            "input_files": ["owner-notes.md"],
            "selected_plan_id": selected_plan_id,
            "selected_native_plan_index": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Selected native plan index does not match selected plan"
