import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.domain.enums import ExecutionRunStatus
from app.domain.models import ExecutionRun
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.main import create_app


class _ExecutionServiceStub:
    def __init__(self, snapshot_payload: dict) -> None:
        self._snapshot_payload = dict(snapshot_payload)

    def get_run(self, run_id: str):
        payload = dict(self._snapshot_payload)
        payload["run_id"] = run_id
        return type("Snapshot", (), payload)()


def _seed_execution_run(database_url: str, *, run_id: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        run = ExecutionRun(
            id=run_id,
            order_id=None,
            machine_id="machine-1",
            viewer_user_id="viewer-1",
            run_kind="order",
            external_order_id="order-ext-1",
            status=ExecutionRunStatus.SUCCEEDED,
            submission_payload={"intent": "stream-test"},
            summary_metrics={},
        )
        session.add(run)
        session.commit()
    engine.dispose()


def _extract_sse_payloads(body: str, *, event_name: str) -> list[dict]:
    payloads: list[dict] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines or lines[0] != f"event: {event_name}":
            continue
        data_line = next((line for line in lines if line.startswith("data: ")), None)
        if data_line is None:
            continue
        payloads.append(json.loads(data_line[6:]))
    return payloads


@pytest.fixture
def stream_client(tmp_path: Path) -> tuple[TestClient, dict]:
    db_path = tmp_path / "execution-stream.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_EXECUTION_SYNC_ENABLED"] = "false"

    run_id = "aso-run-stream-test"
    run_dir = tmp_path / "run-dir"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True)
    planner_log = logs_dir / "planner.log"
    planner_log.write_text("line one\nline two\n", encoding="utf-8")
    stdout_log = run_dir / "stdout.log"
    stdout_log.write_text("stdout line\n", encoding="utf-8")
    stderr_log = run_dir / "stderr.log"
    stderr_log.write_text("stderr line\n", encoding="utf-8")
    events_log = run_dir / "events.ndjson"
    events_log.write_text(
        "\n".join(
            [
                '{"seq":1,"event":"run_started","phase":"starting"}',
                '{"seq":2,"event":"plan_selected","phase":"plan_selection"}',
            ]
        ),
        encoding="utf-8",
    )

    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    stub = _ExecutionServiceStub(
        {
            "external_order_id": "order-ext-1",
            "status": ExecutionRunStatus.SUCCEEDED,
            "run_dir": str(run_dir),
            "stdout_log_path": str(stdout_log),
            "stderr_log_path": str(stderr_log),
            "events_log_path": str(events_log),
        }
    )
    app.dependency_overrides[get_agentskillos_execution_service] = lambda: stub
    with TestClient(app) as test_client:
        _seed_execution_run(os.environ["OUTCOMEX_DATABASE_URL"], run_id=run_id)
        yield test_client, {
            "run_id": run_id,
            "logs_dir": logs_dir,
            "planner_log": planner_log,
        }

    reset_settings_cache()
    reset_container_cache()


def test_execution_run_events_endpoint_returns_items_after_seq(stream_client: tuple[TestClient, dict]) -> None:
    test_client, seeded = stream_client
    response = test_client.get(f"/api/v1/execution-runs/{seeded['run_id']}/events", params={"after_seq": 1})
    assert response.status_code == 200
    payload = response.json()
    assert [item["seq"] for item in payload["items"]] == [2]
    assert payload["next_cursor"] == 2


def test_execution_run_logs_endpoint_lists_files_with_root_path(stream_client: tuple[TestClient, dict]) -> None:
    test_client, seeded = stream_client
    response = test_client.get(f"/api/v1/execution-runs/{seeded['run_id']}/logs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["logs_root_path"] == str(seeded["logs_dir"])
    assert [item["name"] for item in payload["files"]] == ["planner.log", "stdout.log", "stderr.log"]


def test_execution_run_logs_read_endpoint_returns_lines_and_next_offset(stream_client: tuple[TestClient, dict]) -> None:
    test_client, seeded = stream_client
    response = test_client.get(
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/read",
        params={"file": "planner.log", "offset": 0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["file"] == "planner.log"
    assert payload["lines"] == ["line one", "line two"]
    expected_next_offset = len("line one\nline two\n")
    assert payload["next_offset"] == expected_next_offset

    second = test_client.get(
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/read",
        params={"file": "planner.log", "offset": payload["next_offset"]},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["lines"] == []
    assert second_payload["next_offset"] == expected_next_offset


def test_execution_run_stream_endpoint_yields_execution_events(stream_client: tuple[TestClient, dict]) -> None:
    test_client, seeded = stream_client
    with test_client.stream(
        "GET",
        f"/api/v1/execution-runs/{seeded['run_id']}/stream",
        params={"after_seq": 1},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    payloads = _extract_sse_payloads(body, event_name="execution_event")
    assert payloads
    assert payloads[0]["seq"] == 2
    assert payloads[0]["event"] == "plan_selected"


def test_execution_run_logs_stream_endpoint_yields_log_lines(stream_client: tuple[TestClient, dict]) -> None:
    test_client, seeded = stream_client
    with test_client.stream(
        "GET",
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/stream",
        params={"file": "planner.log", "offset": 0},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    payloads = _extract_sse_payloads(body, event_name="log_line")
    assert [item["line"] for item in payloads] == ["line one", "line two"]
    assert payloads[0]["offset"] == 0
    assert payloads[1]["offset"] > payloads[0]["offset"]


def test_execution_run_logs_endpoints_reject_unsafe_and_unknown_file_names(
    stream_client: tuple[TestClient, dict]
) -> None:
    test_client, seeded = stream_client
    unsafe_read = test_client.get(
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/read",
        params={"file": "../planner.log", "offset": 0},
    )
    assert unsafe_read.status_code == 404

    unknown_read = test_client.get(
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/read",
        params={"file": "unknown.log", "offset": 0},
    )
    assert unknown_read.status_code == 404

    unsafe_stream = test_client.get(
        f"/api/v1/execution-runs/{seeded['run_id']}/logs/stream",
        params={"file": "../planner.log", "offset": 0},
    )
    assert unsafe_stream.status_code == 404
