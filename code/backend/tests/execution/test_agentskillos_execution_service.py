import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings
from app.execution.contracts import ExecutionStrategy
from app.execution.observability import read_events_after_seq
import app.integrations.agentskillos_execution_service as execution_module
from app.integrations.agentskillos_execution_service import AgentSkillOSExecutionService


class _BridgeStub:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def resolve_repo_root(self) -> Path:
        return self.repo_root

    def resolve_python_executable(self, _repo_root: Path) -> Path:
        return self.repo_root / ".venv" / "bin" / "python"

    def build_execution_env(self) -> dict[str, str]:
        return {"LLM_MODEL": "openai/qwen3.6-plus"}


def test_execution_service_submit_and_poll_reads_run_record(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    repo_root = tmp_path / "agentskillos"
    (repo_root / ".venv" / "bin").mkdir(parents=True)
    (repo_root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")

    def launcher(command, *, cwd: str, env: dict[str, str]) -> int:
        record_path = Path(command[4])
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "status": "succeeded",
                "workspace_path": "/tmp/workspace",
                "run_dir": "/tmp/run-dir",
                "preview_manifest": [{"path": "workspace/preview.png", "type": "image", "role": "final"}],
                "artifact_manifest": [{"path": "workspace/final.docx", "type": "document", "role": "final"}],
                "skills_manifest": [{"skill_id": "docx", "skill_path": "/skills/docx", "status": "selected"}],
                "model_usage_manifest": [{"provider": "agentskillos_internal", "model": "openai/qwen3.6-plus"}],
                "summary_metrics": {"total_input_tokens": 100, "total_output_tokens": 50},
            }
        )
        record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        assert cwd == str(repo_root)
        assert env["LLM_MODEL"] == "openai/qwen3.6-plus"
        assert command[-1] == "2"
        return 4242

    service = AgentSkillOSExecutionService(
        settings=Settings(
            agentskillos_execution_output_root=str(output_root),
        ),
        bridge=_BridgeStub(repo_root),
        launcher=launcher,
    )

    submitted = service.submit_task(
        external_order_id="order-1",
        prompt="Create report",
        input_files=("brief.md",),
        execution_strategy=ExecutionStrategy.SIMPLICITY,
        selected_plan_index=2,
    )
    assert submitted.run_id.startswith("aso-run-")
    assert submitted.status.value == "succeeded"
    assert submitted.workspace_path == "/tmp/workspace"
    assert submitted.submission_payload == {
        "intent": "Create report",
        "files": ["brief.md"],
        "execution_strategy": "simplicity",
        "selected_plan_index": 2,
    }

    snapshot = service.get_run(submitted.run_id)
    assert snapshot.external_order_id == "order-1"
    assert snapshot.status.value == "succeeded"
    assert snapshot.submission_payload == submitted.submission_payload
    assert snapshot.skills_manifest[0]["skill_id"] == "docx"
    assert snapshot.model_usage_manifest[0]["model"] == "openai/qwen3.6-plus"
    assert snapshot.stdout_log_path.endswith("stdout.log")
    assert snapshot.stderr_log_path.endswith("stderr.log")
    assert snapshot.events_log_path.endswith("events.ndjson")
    assert snapshot.plan_candidates == ()
    assert snapshot.dag is None
    assert snapshot.active_node_id is None
    assert snapshot.logs_root_path is None
    assert snapshot.log_files == ()
    assert snapshot.event_cursor == 0
    assert snapshot.stalled is False
    assert snapshot.stalled_reason is None



def test_get_run_marks_stale_process_as_failed(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "runs"
    run_dir = output_root / "aso-run-stale"
    run_dir.mkdir(parents=True)
    record_path = run_dir / "run.json"
    record_path.write_text(json.dumps({
        "run_id": "aso-run-stale",
        "external_order_id": "order-stale",
        "status": "running",
        "record_path": str(record_path),
        "submission_payload": {"intent": "demo", "files": [], "execution_strategy": "quality"},
        "workspace_path": None,
        "run_dir": None,
        "preview_manifest": [],
        "artifact_manifest": [],
        "skills_manifest": [],
        "model_usage_manifest": [],
        "summary_metrics": {},
        "error": None,
        "started_at": "2026-04-06T07:00:00+00:00",
        "finished_at": None,
        "created_at": "2026-04-06T07:00:00+00:00",
        "pid": 4242,
        "stdout_log_path": str(run_dir / "stdout.log"),
        "stderr_log_path": str(run_dir / "stderr.log"),
        "events_log_path": str(run_dir / "events.ndjson"),
        "last_heartbeat_at": "2026-04-06T07:01:00+00:00",
        "current_phase": "executing",
        "current_step": "render",
    }, indent=2), encoding="utf-8")

    service = AgentSkillOSExecutionService(
        settings=Settings(agentskillos_execution_output_root=str(output_root)),
        bridge=_BridgeStub(tmp_path / "agentskillos"),
        launcher=lambda *args, **kwargs: 0,
    )

    monkeypatch.setattr(execution_module, "_utc_now", lambda: datetime(2026, 4, 6, 7, 2, tzinfo=timezone.utc))
    monkeypatch.setattr(AgentSkillOSExecutionService, "_process_exists", staticmethod(lambda pid: False))

    snapshot = service.get_run("aso-run-stale")

    assert snapshot.status.value == "failed"
    assert snapshot.pid_alive is False
    assert snapshot.current_phase == "failed"
    assert snapshot.error == "process_exited_before_terminal_status"
    persisted = json.loads(record_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["finished_at"] == "2026-04-06T07:02:00+00:00"
    assert persisted["last_heartbeat_at"] == "2026-04-06T07:02:00+00:00"
    assert persisted["current_step"] is None
    assert persisted["last_progress_at"] == "2026-04-06T07:02:00+00:00"


def test_get_run_exposes_observability_snapshot_fields(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "runs"
    run_dir = output_root / "aso-run-observability"
    run_dir.mkdir(parents=True)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir()
    (logs_dir / "planner.log").write_text("planning\\n", encoding="utf-8")
    stdout_log = run_dir / "stdout.log"
    stdout_log.write_text("stdout\\n", encoding="utf-8")
    stderr_log = run_dir / "stderr.log"
    stderr_log.write_text("stderr\\n", encoding="utf-8")
    events_log = run_dir / "events.ndjson"
    events_log.write_text(
        json.dumps(
            {
                "seq": 3,
                "timestamp": "2026-04-06T07:00:30+00:00",
                "run_id": "aso-run-observability",
                "phase": "plan_generation",
                "event": "plan_candidates_generated",
                "level": "info",
                "message": "Generated candidate plans",
                "data": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record_path = run_dir / "run.json"
    record_path.write_text(
        json.dumps(
            {
                "run_id": "aso-run-observability",
                "external_order_id": "order-observability",
                "status": "running",
                "record_path": str(record_path),
                "submission_payload": {"intent": "demo", "files": [], "execution_strategy": "quality"},
                "workspace_path": None,
                "run_dir": str(run_dir),
                "preview_manifest": [],
                "artifact_manifest": [],
                "skills_manifest": [],
                "model_usage_manifest": [],
                "summary_metrics": {},
                "error": None,
                "started_at": "2026-04-06T07:00:00+00:00",
                "finished_at": None,
                "created_at": "2026-04-06T07:00:00+00:00",
                "pid": 9999,
                "stdout_log_path": str(stdout_log),
                "stderr_log_path": str(stderr_log),
                "events_log_path": str(events_log),
                "last_heartbeat_at": "2026-04-06T07:00:10+00:00",
                "last_progress_at": "2026-04-06T07:00:10+00:00",
                "current_phase": "planning",
                "current_step": "select-plan",
                "selected_plan": {"name": "Quality"},
                "plan_candidates": [
                    {"index": 0, "name": "Quality", "description": "Quality path", "strategy": "quality"}
                ],
                "dag": {"nodes": [{"id": "node-1", "name": "node-1", "status": "running"}], "edges": []},
                "active_node_id": "node-1",
                "event_cursor": 3,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    service = AgentSkillOSExecutionService(
        settings=Settings(agentskillos_execution_output_root=str(output_root)),
        bridge=_BridgeStub(tmp_path / "agentskillos"),
        launcher=lambda *args, **kwargs: 0,
    )

    monkeypatch.setattr(execution_module, "_utc_now", lambda: datetime(2026, 4, 6, 7, 2, tzinfo=timezone.utc))
    monkeypatch.setattr(AgentSkillOSExecutionService, "_process_exists", staticmethod(lambda pid: True))

    snapshot = service.get_run("aso-run-observability")

    assert snapshot.logs_root_path == str(logs_dir)
    assert [item["name"] for item in snapshot.log_files] == ["planner.log", "stdout.log", "stderr.log"]
    assert snapshot.event_cursor == 3
    assert snapshot.plan_candidates[0]["name"] == "Quality"
    assert snapshot.dag["nodes"][0]["status"] == "running"
    assert snapshot.active_node_id == "node-1"
    assert snapshot.stalled is True
    assert snapshot.stalled_reason == "no_progress_for_110s"

    persisted_event = json.loads(events_log.read_text(encoding="utf-8").strip())
    assert persisted_event["event"] == "plan_candidates_generated"
    assert set(persisted_event) >= {"seq", "timestamp", "run_id", "phase", "event", "level", "message", "data"}


def test_structured_events_round_trip_from_persisted_file(tmp_path: Path) -> None:
    events_path = tmp_path / "events.ndjson"
    events_path.write_text(
        "\n".join(
            [
                '{"seq":1,"timestamp":"2026-04-06T07:00:00+00:00","run_id":"aso-run-1","phase":"starting","event":"run_started","level":"info","message":"started","data":{}}',
                '{"seq":2,"timestamp":"2026-04-06T07:00:01+00:00","run_id":"aso-run-1","phase":"skill_discovery","event":"skills_discovered","level":"info","message":"Discovered 2 skills","data":{"skills":["a","b"]}}',
            ]
        ),
        encoding="utf-8",
    )

    result = read_events_after_seq(events_path, after_seq=0)

    assert result.next_cursor == 2
    assert len(result.items) == 2
    required = {"seq", "timestamp", "run_id", "phase", "event", "level", "message", "data"}
    assert required.issubset(result.items[0].keys())


def test_embedded_execution_script_compiles() -> None:
    assert "list_log_files(" not in execution_module._EXECUTION_SCRIPT
    assert "discover_log_sources(" in execution_module._EXECUTION_SCRIPT
    assert "initial.update(failed)" in execution_module._EXECUTION_SCRIPT
    assert "initial.update(final)" in execution_module._EXECUTION_SCRIPT
    compile(execution_module._EXECUTION_SCRIPT, "<agentskillos_execution_script>", "exec")



def test_process_exists_treats_zombie_as_dead(monkeypatch) -> None:
    monkeypatch.setattr(execution_module.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(AgentSkillOSExecutionService, "_read_process_state", staticmethod(lambda pid: "Z"))

    assert AgentSkillOSExecutionService._process_exists(4242) is False
