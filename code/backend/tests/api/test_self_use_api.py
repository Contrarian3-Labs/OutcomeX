import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import self_use as self_use_route
from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import ExecutionRunStatus
from app.domain.models import Machine
from app.domain.planning import RecommendedPlan
from app.execution.contracts import ExecutionStrategy
from app.indexer.execution_sync import sync_execution_runs_once
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.main import create_app

OWNER_WALLET = "0x1111111111111111111111111111111111111111"
OTHER_WALLET = "0x2222222222222222222222222222222222222222"


class _ExecutionServiceStub:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.read_run_dir = "/tmp/run-dir"

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
                "workspace_path": str(self.read_run_dir) + "/workspace",
                "run_dir": self.read_run_dir,
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
                "current_step": None,
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
    run_dir = tmp_path / "run-dir"
    workspace_dir = run_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "preview.png").write_bytes(b"fake-png")
    (workspace_dir / "result.md").write_text("# hello", encoding="utf-8")
    stub.read_run_dir = run_dir.as_posix()
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
    payload = response.json()
    with get_container().session_factory() as db:
        machine = db.get(Machine, payload["id"])
        assert machine is not None
        machine.owner_chain_address = OWNER_WALLET.lower()
        db.add(machine)
        db.commit()
        db.refresh(machine)
        payload["owner_chain_address"] = machine.owner_chain_address
    return payload


def _issue_attachment_session(client: TestClient) -> dict:
    response = client.post("/api/v1/attachments/sessions")
    assert response.status_code == 201
    return response.json()


def _upload_attachment(client: TestClient, *, session_id: str, session_token: str) -> str:
    response = client.post(
        "/api/v1/attachments",
        data={"session_id": session_id},
        headers={"X-Attachment-Session-Token": session_token},
        files={"file": ("owner-notes.txt", b"owner specific notes", "text/plain")},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _recommended_plans_for_test(preferred_strategy: ExecutionStrategy | None) -> tuple[RecommendedPlan, ...]:
    plans = (
        RecommendedPlan(
            plan_id="plan-quality",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.QUALITY,
            title="Quality",
            summary="Quality path",
            why_this_plan="For quality",
            tradeoff="Slower",
            native_plan_index=0,
            native_plan_name="Quality",
            native_plan_description="Quality path",
        ),
        RecommendedPlan(
            plan_id="plan-efficiency",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.EFFICIENCY,
            title="Efficiency",
            summary="Efficiency path",
            why_this_plan="For speed",
            tradeoff="Less depth",
            native_plan_index=1,
            native_plan_name="Efficiency",
            native_plan_description="Efficiency path",
        ),
        RecommendedPlan(
            plan_id="plan-simplicity",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.SIMPLICITY,
            title="Simplicity",
            summary="Simplicity path",
            why_this_plan="For lean flow",
            tradeoff="Least checks",
            native_plan_index=2,
            native_plan_name="Simplicity",
            native_plan_description="Simplicity path",
        ),
    )
    if preferred_strategy is None:
        return plans
    preferred = [plan for plan in plans if plan.strategy == preferred_strategy]
    remaining = [plan for plan in plans if plan.strategy != preferred_strategy]
    return tuple(preferred + remaining)


def test_self_use_plans_forbid_non_owner(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)

    response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_wallet_address": OTHER_WALLET,
            "machine_id": machine["id"],
            "prompt": "Create a private diagnostics report",
            "execution_strategy": "quality",
        },
    )

    assert response.status_code == 403


def test_self_use_plans_resolve_uploaded_attachments_to_real_paths_for_planning(
    client: tuple[TestClient, _ExecutionServiceStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)
    session = _issue_attachment_session(test_client)
    attachment_id = _upload_attachment(
        test_client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )
    captured_path: dict[str, Path] = {}

    def _stub_build_recommended_plans(  # noqa: PLR0913
        *,
        user_id: str,
        chat_session_id: str,
        user_message: str,
        preferred_strategy: ExecutionStrategy | None,
        input_files: tuple[str, ...],
        planning_context_key: str = "",
    ) -> tuple[RecommendedPlan, ...]:
        assert user_id == OWNER_WALLET.lower()
        assert chat_session_id == f"self-use:{machine['id']}:{OWNER_WALLET.lower()}"
        assert user_message == "Build owner dashboard with uploaded notes"
        assert len(input_files) == 1
        assert planning_context_key.startswith("ctx_")
        resolved = Path(input_files[0])
        assert resolved.exists()
        assert resolved.read_bytes() == b"owner specific notes"
        captured_path["value"] = resolved
        return _recommended_plans_for_test(preferred_strategy)

    monkeypatch.setattr(self_use_route, "build_recommended_plans", _stub_build_recommended_plans)

    response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_wallet_address": OWNER_WALLET,
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard with uploaded notes",
            "execution_strategy": "quality",
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )

    assert response.status_code == 200
    assert response.json()["input_files"] == []
    assert response.json()["attachment_session_id"] == session["session_id"]
    assert response.json()["attachment_ids"] == [attachment_id]
    assert response.json()["planning_context_id"].startswith("ctx_")
    assert "value" in captured_path
    assert not captured_path["value"].exists()


def test_self_use_run_dispatches_resolved_attachment_inputs_and_persists_context(
    client: tuple[TestClient, _ExecutionServiceStub],
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    session = _issue_attachment_session(test_client)
    attachment_id = _upload_attachment(
        test_client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )

    response = test_client.post(
        "/api/v1/self-use/runs",
        json={
            "viewer_wallet_address": OWNER_WALLET,
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard from uploaded context",
            "execution_strategy": "quality",
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )

    assert response.status_code == 201
    assert len(stub.submit_calls) == 1
    dispatched_files = stub.submit_calls[0]["input_files"]
    assert len(dispatched_files) == 1
    resolved = Path(dispatched_files[0])
    assert "outcomex-execution-attachments-" in dispatched_files[0]
    assert resolved.exists()
    assert resolved.read_bytes() == b"owner specific notes"
    payload = response.json()["submission_payload"]
    assert payload["files"] == dispatched_files
    assert payload["planning_context_id"].startswith("ctx_")
    assert payload["planning_attachment_session_id"] == session["session_id"]
    assert payload["planning_attachment_ids"] == [attachment_id]


def test_self_use_owner_flow_without_order_side_effects(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    owner_user_id = "owner-1"
    owner_wallet = OWNER_WALLET

    plans_response = test_client.post(
        "/api/v1/self-use/plans",
        json={
            "viewer_wallet_address": owner_wallet,
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
            "viewer_wallet_address": owner_wallet,
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
    assert run_payload["viewer_wallet_address"] == owner_wallet.lower()
    assert run_payload["viewer_user_id"] is None
    assert run_payload["external_order_id"] == f"self-use:{machine['id']}:{owner_wallet.lower()}"
    assert run_payload["submission_payload"]["selected_plan_index"] == 2
    assert stub.submit_calls[0]["selected_plan_index"] == 2

    after_orders = test_client.get("/api/v1/orders", params={"user_id": owner_user_id})
    assert after_orders.status_code == 200
    assert after_orders.json()["items"] == []

    read_run = test_client.get(
        f"/api/v1/self-use/runs/{run_payload['id']}",
        params={"viewer_wallet_address": owner_wallet},
    )
    assert read_run.status_code == 200
    read_payload = read_run.json()
    assert read_payload["id"] == run_payload["id"]
    assert read_payload["run_kind"] == "self_use"
    assert read_payload["order_id"] is None
    assert read_payload["machine_id"] == machine["id"]
    assert read_payload["viewer_wallet_address"] == owner_wallet.lower()
    assert read_payload["viewer_user_id"] is None
    assert read_payload["status"] == "succeeded"
    assert read_payload["current_phase"] == "finished"
    assert read_payload["current_step"] == "Completed"

    forbidden_read = test_client.get(
        f"/api/v1/self-use/runs/{run_payload['id']}",
        params={"viewer_wallet_address": OTHER_WALLET},
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
            "viewer_wallet_address": OWNER_WALLET,
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
            "viewer_wallet_address": OWNER_WALLET,
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


def test_self_use_artifact_file_endpoint_serves_preview_and_blocks_unknown_paths(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)

    create_run = test_client.post(
        "/api/v1/self-use/runs",
        json={
            "viewer_wallet_address": OWNER_WALLET,
            "machine_id": machine["id"],
            "prompt": "Build owner dashboard",
            "execution_strategy": "simplicity",
            "input_files": ["owner-notes.md"],
        },
    )
    assert create_run.status_code == 201
    run_id = create_run.json()["id"]

    preview = test_client.get(
        f"/api/v1/self-use/runs/{run_id}/artifacts/file",
        params={"viewer_wallet_address": OWNER_WALLET, "path": "workspace/preview.png"},
    )
    assert preview.status_code == 200
    assert preview.content == b"fake-png"

    missing = test_client.get(
        f"/api/v1/self-use/runs/{run_id}/artifacts/file",
        params={"viewer_wallet_address": OWNER_WALLET, "path": "workspace/unknown.png"},
    )
    assert missing.status_code == 404
