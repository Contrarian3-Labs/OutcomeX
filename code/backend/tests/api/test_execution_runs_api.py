import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.domain.enums import ExecutionRunStatus
from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.onchain.lifecycle_service import BroadcastReceipt, get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriteResult, get_order_writer
from app.main import create_app
from app.domain.models import ExecutionRun, Order
from app.api.routes.execution_runs import _resolve_display_current_step
from app.runtime.hardware_simulator import WorkloadSpec, get_shared_hardware_simulator


class _LifecycleSpy:
    def __init__(self) -> None:
        self.calls = []
        self.minted = []

    def enabled(self) -> bool:
        return True

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str):
        self.minted.append((owner_user_id, token_uri))
        return type(
            "Minted",
            (),
            {
                "tx_hash": "0xmint",
                "receipt": None,
                "onchain_machine_id": "1",
            },
        )()

    def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> BroadcastReceipt:
        self.calls.append((user_id, write_result.method_name, write_result.payload))
        return BroadcastReceipt(tx_hash="0xpreview", receipt=None)


def test_resolve_display_current_step_falls_back_to_phase_labels() -> None:
    assert _resolve_display_current_step(ExecutionRunStatus.QUEUED, None) == "Queued"
    assert _resolve_display_current_step(ExecutionRunStatus.PLANNING, None) == "Planning"
    assert _resolve_display_current_step(ExecutionRunStatus.RUNNING, None) == "Running"
    assert _resolve_display_current_step(ExecutionRunStatus.SUCCEEDED, None) == "Completed"


class _WriterSpy:
    def __init__(self) -> None:
        self.create_calls = []
        self.paid_calls = []

    def create_order(self, order, *, buyer_wallet_address):
        self.create_calls.append((order.id, buyer_wallet_address))
        return OrderWriteResult(
            tx_hash="0xcreateorder",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="createOrderByAdapter",
            idempotency_key="create-order",
            payload={"buyer": buyer_wallet_address, "machine_id": order.machine_id, "gross_amount": order.quoted_amount_cents},
        )

    def mark_order_paid(self, order, payment) -> None:
        self.paid_calls.append((order.id, payment.id))

    def mark_preview_ready(self, order, *, valid_preview: bool = True) -> OrderWriteResult:
        return OrderWriteResult(
            tx_hash="0xsynthetic-preview",
            submitted_at=datetime.now(timezone.utc),
            chain_id=133,
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            method_name="markPreviewReady",
            idempotency_key="preview",
            payload={"order_id": order.onchain_order_id, "valid_preview": valid_preview},
        )


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
        if run_id == "aso-run-self-use":
            return type(
                "Snapshot",
                (),
                {
                    "run_id": run_id,
                    "external_order_id": "self-use:machine-owner-2:0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
                    "status": ExecutionRunStatus.RUNNING,
                    "submission_payload": {
                        "intent": "Generate a cat image",
                        "files": [],
                        "execution_strategy": "quality",
                        "selected_plan_index": 0,
                    },
                    "selected_plan": None,
                    "workspace_path": None,
                    "run_dir": None,
                    "preview_manifest": (),
                    "artifact_manifest": (),
                    "skills_manifest": (),
                    "model_usage_manifest": (),
                    "summary_metrics": {},
                    "error": None,
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                    "pid": 814977,
                    "pid_alive": True,
                    "stdout_log_path": None,
                    "stderr_log_path": None,
                    "events_log_path": None,
                    "last_heartbeat_at": datetime.now(timezone.utc),
                    "current_phase": "executing",
                    "current_step": "phase-1",
                    "plan_candidates": [],
                    "dag": {"nodes": [{"id": "generate-image", "status": "running"}], "edges": []},
                    "active_node_id": "generate-image",
                    "logs_root_path": None,
                    "log_files": [],
                    "event_cursor": 9,
                    "last_progress_at": datetime.now(timezone.utc),
                    "stalled": False,
                    "stalled_reason": None,
                },
            )()
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
                "pid": 1234,
                "pid_alive": True,
                "stdout_log_path": "/tmp/stdout.log",
                "stderr_log_path": "/tmp/stderr.log",
                "events_log_path": "/tmp/events.ndjson",
                "last_heartbeat_at": datetime.now(timezone.utc),
                "current_phase": "finished",
                "current_step": "docx",
                "plan_candidates": [
                    {"index": 0, "name": "Quality-First", "description": "Deep validation path", "strategy": "quality"},
                    {"index": 1, "name": "Efficiency-First", "description": "Fast path", "strategy": "efficiency"},
                ],
                "dag": {"nodes": [{"id": "n1", "status": "running"}], "edges": []},
                "active_node_id": "n1",
                "logs_root_path": "/tmp/run-dir/logs",
                "log_files": [
                    {
                        "kind": "raw_file",
                        "name": "planner.log",
                        "path": "/tmp/run-dir/logs/planner.log",
                        "size": 10,
                        "updated_at": datetime.now(timezone.utc),
                    }
                ],
                "event_cursor": 12,
                "last_progress_at": datetime.now(timezone.utc),
                "stalled": False,
                "stalled_reason": None,
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
    os.environ["OUTCOMEX_EXECUTION_SYNC_ENABLED"] = "false"
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = '{"user-1":"0x00000000000000000000000000000000000000aa"}'
    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    stub = _ExecutionServiceStub()
    lifecycle = _LifecycleSpy()
    writer = _WriterSpy()
    app.dependency_overrides[get_agentskillos_execution_service] = lambda: stub
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: lifecycle
    app.dependency_overrides[get_order_writer] = lambda: writer
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


def _create_paid_order(
    client: TestClient,
    machine_id: str,
    *,
    execution_strategy: str = "quality",
    input_files: list[str] | None = None,
) -> dict:
    if input_files is None:
        input_files = ["brief.md"]
    order = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Write a report",
            "quoted_amount_cents": 1000,
            "input_files": input_files,
            "execution_strategy": execution_strategy,
        },
    )
    assert order.status_code == 201
    order_id = order.json()["id"]

    payment_intent = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": 1000, "currency": "USDC"},
    )
    assert payment_intent.status_code == 201
    payment_id = payment_intent.json()["payment_id"]
    confirm = client.post(f"/api/v1/payments/{payment_id}/mock-confirm", json={"state": "succeeded"})
    assert confirm.status_code == 200
    _project_authoritative_order(
        order_id,
        onchain_order_id=f"paid-{order_id}",
        status="PAID",
        paid_projection=True,
    )
    return order.json()


def _create_paid_order_without_anchor(client: TestClient, machine_id: str) -> dict:
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
        json={"amount_cents": 1000, "currency": "USDC"},
    )
    assert payment_intent.status_code == 201
    payment_id = payment_intent.json()["payment_id"]
    confirm = client.post(f"/api/v1/payments/{payment_id}/mock-confirm", json={"state": "succeeded"})
    assert confirm.status_code == 200
    return order.json()


def _project_authoritative_order(
    order_id: str,
    *,
    onchain_order_id: str,
    status: str,
    paid_projection: bool,
    cancelled_as_expired: bool | None = None,
) -> None:
    engine = create_engine(os.environ["OUTCOMEX_DATABASE_URL"])
    with Session(engine) as session:
        order = session.scalar(select(Order).where(Order.id == order_id))
        assert order is not None
        order.onchain_order_id = onchain_order_id
        order.create_order_tx_hash = f"0xtx-{onchain_order_id}"
        order.create_order_event_id = f"OrderCreated:{onchain_order_id}:0xtx-{onchain_order_id}"
        order.create_order_block_number = 12345
        metadata = dict(order.execution_metadata or {})
        metadata["authoritative_order_status"] = status
        metadata["authoritative_paid_projection"] = paid_projection
        if cancelled_as_expired is not None:
            metadata["cancelled_as_expired"] = cancelled_as_expired
        order.execution_metadata = metadata
        session.add(order)
        session.commit()
    engine.dispose()


def test_start_execution_creates_run_and_run_endpoint_returns_snapshot_without_mutating_order_state(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
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
        "order_selected_plan_id": start.json()["submission_payload"]["selected_plan_id"],
        "order_selected_plan_strategy": "quality",
        "order_selected_plan_index": 0,
        "order_input_files": ["brief.md"],
        "submission_payload_selected_plan_id": start.json()["submission_payload"]["selected_plan_id"],
        "submission_payload_execution_strategy": "quality",
        "submission_payload_files": ["brief.md"],
        "submission_payload_selected_plan_index": 0,
        "selected_plan_index": 0,
        "selected_plan_name": "Quality-First",
        "selected_plan_strategy_matches": True,
        "input_files_match": True,
        "selected_plan_id_present": True,
        "is_consistent": True,
    }

    run_response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert run_response.status_code == 200
    payload = run_response.json()
    order_fetch = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_fetch.status_code == 200
    assert payload["status"] == "succeeded"
    assert payload["submission_payload"]["execution_strategy"] == "quality"
    assert payload["submission_payload"]["selected_plan_id"] == order_fetch.json()["execution_metadata"]["selected_plan_id"]
    assert payload["submission_payload"]["selected_plan_index"] == 0
    assert payload["selected_plan"]["index"] == 0
    assert payload["selected_plan"]["name"] == "Quality-First"
    assert payload["selected_plan"]["description"] == "Deep validation path"
    assert payload["selected_plan"]["nodes"][0]["name"] == "docx"
    assert payload["selected_plan_binding"] == {
        "order_selected_plan_id": order_fetch.json()["execution_metadata"]["selected_plan_id"],
        "order_selected_plan_strategy": "quality",
        "order_selected_plan_index": 0,
        "order_input_files": ["brief.md"],
        "submission_payload_selected_plan_id": order_fetch.json()["execution_metadata"]["selected_plan_id"],
        "submission_payload_execution_strategy": "quality",
        "submission_payload_files": ["brief.md"],
        "submission_payload_selected_plan_index": 0,
        "selected_plan_index": 0,
        "selected_plan_name": "Quality-First",
        "selected_plan_strategy_matches": True,
        "input_files_match": True,
        "selected_plan_id_present": True,
        "is_consistent": True,
    }
    assert payload["artifact_manifest"][0]["path"] == "workspace/report.docx"
    assert payload["skills_manifest"][0]["skill_id"] == "docx"
    assert payload["pid"] == 1234
    assert payload["pid_alive"] is True
    assert payload["stdout_log_path"] == "/tmp/stdout.log"
    assert payload["stderr_log_path"] == "/tmp/stderr.log"
    assert payload["events_log_path"] == "/tmp/events.ndjson"
    assert payload["current_phase"] == "finished"
    assert payload["current_step"] == "docx"
    assert payload["last_heartbeat_at"] is not None

    assert order_fetch.json()["execution_metadata"]["run_id"] == "aso-run-test"
    assert order_fetch.json()["execution_metadata"]["run_status"] == "queued"
    assert order_fetch.json()["state"] == "executing"

    polled_again = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert polled_again.status_code == 200

    order_after_second_poll = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_second_poll.status_code == 200
    assert order_after_second_poll.json()["state"] == "executing"
    assert order_after_second_poll.json()["settlement_state"] == "not_ready"


def test_execution_run_snapshot_includes_observability_fields(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])
    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200

    response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert response.status_code == 200
    payload = response.json()

    assert "plan_candidates" in payload
    assert "dag" in payload
    assert "active_node_id" in payload
    assert "logs_root_path" in payload
    assert "log_files" in payload
    assert "event_cursor" in payload
    assert "last_progress_at" in payload
    assert "stalled" in payload
    assert payload["event_cursor"] == 12
    assert payload["active_node_id"] == "n1"
    assert payload["stalled"] is False
    assert isinstance(payload["log_files"], list)
    assert payload["plan_candidates"][0]["index"] == 0
    assert payload["plan_candidates"][0]["name"] == "Quality-First"
    assert payload["plan_candidates"][0]["description"] == "Deep validation path"
    assert payload["plan_candidates"][0]["strategy"] == "quality"
    assert payload["log_files"][0]["kind"] == "raw_file"
    assert payload["log_files"][0]["name"] == "planner.log"
    assert payload["log_files"][0]["path"] == "/tmp/run-dir/logs/planner.log"
    assert payload["log_files"][0]["size"] == 10
    assert payload["log_files"][0]["updated_at"] is not None


def test_execution_run_snapshot_coerces_invalid_observability_scalars_safely(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])
    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200

    malformed_snapshot = stub.get_run("aso-run-test")
    malformed_snapshot.event_cursor = "not-an-int"
    malformed_snapshot.stalled = "false"
    stub.get_run = lambda _run_id: malformed_snapshot  # type: ignore[assignment]

    response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["event_cursor"] == 0
    assert payload["stalled"] is False


def test_execution_run_snapshot_normalizes_malformed_plan_candidates_and_log_files(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])
    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200

    malformed_snapshot = stub.get_run("aso-run-test")
    malformed_snapshot.plan_candidates = [
        None,
        "bad",
        {"index": "7", "name": 901, "description": None, "strategy": "quality"},
        {"name": "Missing Index"},
    ]
    malformed_snapshot.log_files = [
        "bad",
        {
            "kind": "raw_file",
            "name": 33,
            "path": "/tmp/run-dir/logs/raw.log",
            "size": "42",
            "updated_at": "2026-04-09T12:30:00Z",
        },
        {"path": "/tmp/run-dir/logs/minimal.log"},
    ]
    stub.get_run = lambda _run_id: malformed_snapshot  # type: ignore[assignment]

    response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert response.status_code == 200
    payload = response.json()

    assert payload["plan_candidates"] == [
        {"index": 7, "name": "901", "description": "", "strategy": "quality"},
        {"index": 0, "name": "Missing Index", "description": "", "strategy": ""},
    ]
    assert payload["log_files"][0]["kind"] == "raw_file"
    assert payload["log_files"][0]["name"] == "33"
    assert payload["log_files"][0]["path"] == "/tmp/run-dir/logs/raw.log"
    assert payload["log_files"][0]["size"] == 42
    assert payload["log_files"][0]["updated_at"] == "2026-04-09T12:30:00Z"
    assert payload["log_files"][1] == {
        "kind": "",
        "name": "",
        "path": "/tmp/run-dir/logs/minimal.log",
        "size": 0,
        "updated_at": None,
    }


def test_start_execution_rejects_tampered_execution_contract(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    engine = create_engine(os.environ["OUTCOMEX_DATABASE_URL"])
    with Session(engine) as session:
        db_order = session.get(Order, order["id"])
        assert db_order is not None
        execution_request = dict(db_order.execution_request or {})
        execution_request["execution_strategy"] = "efficiency"
        db_order.execution_request = execution_request
        session.add(db_order)
        session.commit()
    engine.dispose()

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order execution contract is inconsistent"


def test_start_execution_requires_authoritative_onchain_order_anchor(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, _stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order_without_anchor(test_client, machine["id"])

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order execution requires authoritative paid projection"


def test_start_execution_rejects_anchor_without_authoritative_paid_projection(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order_without_anchor(test_client, machine["id"])
    _project_authoritative_order(order["id"], onchain_order_id="9004", status="CREATED", paid_projection=False)

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order execution requires authoritative paid projection"
    assert stub.submit_calls == 0


def test_start_execution_rejects_projected_expired_order(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = test_client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Write a report",
            "quoted_amount_cents": 1000,
            "input_files": ["brief.md"],
            "execution_strategy": "quality",
        },
    )
    assert order.status_code == 201

    engine = create_engine(os.environ["OUTCOMEX_DATABASE_URL"])
    with Session(engine) as session:
        db_order = session.scalar(select(Order).where(Order.id == order.json()["id"]))
        assert db_order is not None
        db_order.onchain_order_id = "9002"
        db_order.create_order_tx_hash = "0xtx-9002"
        db_order.create_order_event_id = "OrderCreated:9002:0xtx-9002"
        db_order.create_order_block_number = 12346
        db_order.state = __import__("app.domain.enums", fromlist=["OrderState"]).OrderState.CANCELLED
        db_order.cancelled_at = datetime.now(timezone.utc)
        db_order.preview_state = __import__("app.domain.enums", fromlist=["PreviewState"]).PreviewState.EXPIRED
        metadata = dict(db_order.execution_metadata or {})
        metadata["authoritative_order_status"] = "CANCELLED"
        metadata["authoritative_paid_projection"] = False
        metadata["cancelled_as_expired"] = True
        db_order.execution_metadata = metadata
        session.add(db_order)
        session.commit()
    engine.dispose()

    start = test_client.post(f"/api/v1/orders/{order.json()['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order is expired"
    assert stub.submit_calls == 0


def test_start_execution_rejects_projected_cancelled_order(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = test_client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Write a report",
            "quoted_amount_cents": 1000,
            "input_files": ["brief.md"],
            "execution_strategy": "quality",
        },
    )
    assert order.status_code == 201

    engine = create_engine(os.environ["OUTCOMEX_DATABASE_URL"])
    with Session(engine) as session:
        db_order = session.scalar(select(Order).where(Order.id == order.json()["id"]))
        assert db_order is not None
        db_order.onchain_order_id = "9003"
        db_order.create_order_tx_hash = "0xtx-9003"
        db_order.create_order_event_id = "OrderCreated:9003:0xtx-9003"
        db_order.create_order_block_number = 12347
        db_order.state = __import__("app.domain.enums", fromlist=["OrderState"]).OrderState.CANCELLED
        db_order.cancelled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        metadata = dict(db_order.execution_metadata or {})
        metadata["authoritative_order_status"] = "CANCELLED"
        metadata["authoritative_paid_projection"] = False
        metadata["cancelled_as_expired"] = False
        db_order.execution_metadata = metadata
        session.add(db_order)
        session.commit()
    engine.dispose()

    start = test_client.post(f"/api/v1/orders/{order.json()['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order is cancelled"
    assert stub.submit_calls == 0


def test_start_execution_rejects_projected_unavailable_order(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    simulator = get_shared_hardware_simulator(machine["id"])
    running = simulator.submit(
        WorkloadSpec(
            workload_id="running-capacity",
            capacity_units=24,
            memory_mb=32_768,
            duration_ticks=5,
        )
    )
    assert running.status.value == "running"
    for idx in range(8):
        queued = simulator.submit(
            WorkloadSpec(
                workload_id=f"queued-{idx}",
                capacity_units=1,
                memory_mb=64,
                duration_ticks=1,
            )
        )
        assert queued.status.value == "queued"

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 409
    assert start.json()["detail"] == "Order machine is unavailable"
    assert stub.submit_calls == 0


def test_start_execution_allows_queueable_busy_runtime(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    running = get_shared_hardware_simulator(machine["id"]).submit(
        WorkloadSpec(
            workload_id="busy-but-queueable",
            capacity_units=24,
            memory_mb=32_768,
            duration_ticks=5,
        )
    )
    assert running.status.value == "running"

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200
    assert start.json()["status"] == "queued"
    assert stub.submit_calls == 1


def test_start_execution_preflight_uses_real_strategy_and_input_files(client: tuple[TestClient, _ExecutionServiceStub]) -> None:
    test_client, stub = client
    machine = _create_machine(test_client)
    light_order = _create_paid_order(
        test_client,
        machine["id"],
        execution_strategy="simplicity",
        input_files=[],
    )
    heavy_order = _create_paid_order(
        test_client,
        machine["id"],
        execution_strategy="quality",
        input_files=["brief.md", "appendix.md"],
    )

    simulator = get_shared_hardware_simulator(machine["id"])
    running = simulator.submit(
        WorkloadSpec(
            workload_id="partial-saturation",
            capacity_units=23,
            memory_mb=32_000,
            duration_ticks=5,
        )
    )
    assert running.status.value == "running"
    for idx in range(8):
        queued = simulator.submit(
            WorkloadSpec(
                workload_id=f"queue-full-{idx}",
                capacity_units=2,
                memory_mb=64,
                duration_ticks=1,
            )
        )
        assert queued.status.value == "queued"

    light = test_client.post(f"/api/v1/orders/{light_order['id']}/start-execution")
    assert light.status_code == 200
    assert light.json()["status"] == "queued"

    heavy = test_client.post(f"/api/v1/orders/{heavy_order['id']}/start-execution")
    assert heavy.status_code == 409
    assert heavy.json()["detail"] == "Order machine is unavailable"
    assert stub.submit_calls == 1


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


def test_execution_run_get_does_not_broadcast_preview_ready_when_onchain_order_exists(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])

    from app.core.container import get_container
    container = get_container()
    with container.session_factory() as db:
        db_order = db.get(__import__("app.domain.models", fromlist=["Order"]).Order, order["id"])
        db_machine = db.get(__import__("app.domain.models", fromlist=["Machine"]).Machine, machine["id"])
        db_order.onchain_order_id = "42"
        db_order.create_order_tx_hash = "0xpaid"
        db_machine.onchain_machine_id = "7"
        db_machine.has_active_tasks = True
        db.commit()

    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200

    run_response = test_client.get("/api/v1/execution-runs/aso-run-test")
    assert run_response.status_code == 200
    order_fetch = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_fetch.status_code == 200
    payload = order_fetch.json()
    assert "onchain_preview_ready_tx_hash" not in payload["execution_metadata"]
    assert payload["state"] == "executing"


def test_list_execution_runs_includes_machine_scoped_self_use_runs(
    client: tuple[TestClient, _ExecutionServiceStub]
) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)

    engine = create_engine(os.environ["OUTCOMEX_DATABASE_URL"])
    with Session(engine) as session:
        session.add(
            ExecutionRun(
                id="aso-run-self-use",
                order_id=None,
                machine_id=machine["id"],
                viewer_user_id="0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
                run_kind="self_use",
                external_order_id=f"self-use:{machine['id']}:0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
                status=ExecutionRunStatus.RUNNING,
                submission_payload={"intent": "Generate a cat image", "execution_strategy": "quality", "files": []},
            )
        )
        session.commit()
    engine.dispose()

    response = test_client.get(f"/api/v1/execution-runs?machine_id={machine['id']}")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "aso-run-self-use"
    assert payload[0]["machine_id"] == machine["id"]
    assert payload[0]["run_kind"] == "self_use"
    assert payload[0]["viewer_wallet_address"] == "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
    assert payload[0]["submission_payload"]["intent"] == "Generate a cat image"
