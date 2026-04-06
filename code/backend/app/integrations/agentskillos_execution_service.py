"""Service wrapper that treats AgentSkillOS as the execution kernel."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.config import Settings, get_settings
from ..domain.enums import ExecutionRunStatus
from ..execution.contracts import ExecutionStrategy
from .agentskillos_bridge import AgentSkillOSBridge

_EXECUTION_SCRIPT = """
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(sys.argv[1])
record_path = Path(sys.argv[2])
base_dir = Path(sys.argv[3])
external_order_id = sys.argv[4]
task_prompt = sys.argv[5]
mode = sys.argv[6]
skill_group = sys.argv[7]
files = json.loads(sys.argv[8])
execution_strategy = sys.argv[9]
selected_plan_index = int(sys.argv[10])

sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import config
config.Config.reset()


def apply_runtime_model_override():
    runtime_model = (
        os.getenv("AGENTSKILLOS_RUNTIME_MODEL", "").strip()
        or os.getenv("LLM_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or os.getenv("ANTHROPIC_MODEL", "").strip()
    )
    if not runtime_model:
        return
    cfg = config.get_config()
    orchestrators = config.Config._yaml_cache.setdefault("orchestrators", {})
    for name in ("dag", "free-style", "no-skill"):
        orchestrators.setdefault(name, {}).setdefault("runtime", {})["model"] = runtime_model


apply_runtime_model_override()
from constants import resolve_skill_group
from orchestrator.registry import create_engine
from orchestrator.visualizers import NullVisualizer
from workflow.anchor_policy import TaskAnchorIntent, infer_required_skills, merge_skills
from workflow.models import TaskRequest
from workflow.service import discover_skills, run_task


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_record(payload):
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_event(event_type, **fields):
    events_path = Path(initial.get("events_log_path") or (record_path.parent / "events.ndjson"))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": utc_now(), "event": event_type, **fields}
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\\n")


def heartbeat(payload, *, phase, step=None):
    payload["last_heartbeat_at"] = utc_now()
    payload["current_phase"] = phase
    payload["current_step"] = step
    write_record(payload)
    append_event("heartbeat", phase=phase, step=step)


def classify_artifact(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix in {".pptx"}:
        return "presentation"
    if suffix in {".docx", ".pdf", ".md"}:
        return "document"
    if suffix in {".xlsx", ".csv"}:
        return "spreadsheet"
    if suffix in {".html"}:
        return "html"
    if suffix in {".json"}:
        return "json"
    if suffix in {".pkl"}:
        return "model"
    return "file"


def choose_preview(artifacts):
    preferred = {"image", "video", "html", "presentation", "document"}
    return [artifact for artifact in artifacts if artifact["type"] in preferred][:5]


def default_plan_index(strategy: str) -> int:
    if strategy == "efficiency":
        return 1
    if strategy == "simplicity":
        return 2
    return 0


async def execute_with_selected_native_plan():
    required_skills = infer_required_skills(
        TaskAnchorIntent(task=task_prompt, files=list(files or []), required_skills=[])
    )
    discovered_skills = discover_skills(task_prompt, skill_group=skill_group)
    skills = merge_skills(required_skills=required_skills, discovered_skills=discovered_skills)
    skill_group_cfg = resolve_skill_group(skill_group)
    request = TaskRequest(
        task=task_prompt,
        mode=mode,
        skill_group=skill_group,
        files=files or None,
        base_dir=str(base_dir),
        task_id=external_order_id,
    )
    if mode != "dag":
        await run_task(request)
        return skills, None

    from orchestrator.runtime.run_context import RunContext

    run_context = RunContext.create(
        task=task_prompt,
        mode=mode,
        task_id=external_order_id,
        base_dir=str(base_dir),
    )
    engine = create_engine(mode, run_context=run_context, skill_dir=skill_group_cfg["skills_dir"], allowed_tools=None)
    visualizer = NullVisualizer(
        auto_select_plan=selected_plan_index if selected_plan_index >= 0 else default_plan_index(execution_strategy)
    )
    result = await engine.run_with_visualizer(
        task=task_prompt,
        skill_names=skills,
        visualizer=visualizer,
        files=list(files or []) or None,
    )
    plan_payload = None
    plan_path = run_context.run_dir / "plan.json"
    if plan_path.exists():
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    return skills, plan_payload


async def main():
    global initial
    initial = json.loads(record_path.read_text(encoding="utf-8"))
    initial["status"] = "running"
    initial["started_at"] = utc_now()
    heartbeat(initial, phase="starting")
    append_event("run_started", external_order_id=external_order_id, execution_strategy=execution_strategy)

    before_dirs = {p.name for p in base_dir.iterdir()} if base_dir.exists() else set()
    base_dir.mkdir(parents=True, exist_ok=True)

    try:
        heartbeat(initial, phase="planning")
        discovered_skills, selected_plan = await execute_with_selected_native_plan()
        heartbeat(initial, phase="collecting_artifacts")
    except Exception as exc:
        failed = dict(initial)
        failed["status"] = "failed"
        failed["error"] = f"{exc.__class__.__name__}: {exc}"
        failed["finished_at"] = utc_now()
        failed["current_phase"] = "failed"
        failed["last_heartbeat_at"] = utc_now()
        write_record(failed)
        append_event("run_failed", error=failed["error"])
        raise

    candidates = [p for p in base_dir.iterdir() if p.is_dir() and p.name not in before_dirs]
    if not candidates:
        candidates = [p for p in base_dir.iterdir() if p.is_dir()]
    run_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    workspace_dir = run_dir / "workspace"
    meta_path = run_dir / "meta.json"
    result_path = run_dir / "result.json"

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    sdk_metrics = result.get("sdk_metrics") or {}

    artifacts = []
    if workspace_dir.exists():
        for path in sorted(p for p in workspace_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(run_dir).as_posix()
            artifacts.append(
                {
                    "path": rel,
                    "type": classify_artifact(path),
                    "role": "final",
                }
            )

    skill_group_cfg = resolve_skill_group(skill_group)
    skills_dir = Path(skill_group_cfg["skills_dir"])
    skills = [
        {
            "skill_id": skill_id,
            "skill_path": str((skills_dir / skill_id).resolve()),
            "status": "selected",
        }
        for skill_id in meta.get("skills", discovered_skills or [])
    ]

    llm_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL") or ""
    model_usage = []
    if llm_model or sdk_metrics:
        model_usage.append(
            {
                "provider": "agentskillos_internal",
                "model": llm_model,
                "input_tokens": int(sdk_metrics.get("input_tokens", 0) or 0),
                "output_tokens": int(sdk_metrics.get("output_tokens", 0) or 0),
                "estimated_cost_usd": float(sdk_metrics.get("total_cost_usd", 0.0) or 0.0),
            }
        )

    summary_metrics = {
        "total_input_tokens": int(sdk_metrics.get("input_tokens", 0) or 0),
        "total_output_tokens": int(sdk_metrics.get("output_tokens", 0) or 0),
        "total_estimated_cost_usd": float(sdk_metrics.get("total_cost_usd", 0.0) or 0.0),
        "total_models_used": len(model_usage),
        "total_skills_used": len(skills),
    }

    submission_payload = {
        "intent": task_prompt,
        "files": files,
        "execution_strategy": execution_strategy,
    }
    if selected_plan_index >= 0:
        submission_payload["selected_plan_index"] = selected_plan_index

    final = dict(initial)
    final.update(
        {
            "status": "succeeded" if result.get("status") == "completed" else "failed",
            "run_dir": str(run_dir),
            "workspace_path": str(workspace_dir),
            "submission_payload": submission_payload,
            "artifact_manifest": artifacts,
            "preview_manifest": choose_preview(artifacts),
            "skills_manifest": skills,
            "model_usage_manifest": model_usage,
            "summary_metrics": summary_metrics,
            "error": result.get("error"),
            "finished_at": utc_now(),
            "last_heartbeat_at": utc_now(),
            "current_phase": "finished" if result.get("status") == "completed" else "failed",
            "current_step": None,
        }
    )
    if selected_plan is not None:
        final["selected_plan"] = selected_plan
    write_record(final)
    append_event("run_finished", status=final["status"], error=final.get("error"))


asyncio.run(main())
""".strip()


@dataclass(frozen=True)
class ExecutionRunSnapshot:
    run_id: str
    external_order_id: str
    status: ExecutionRunStatus
    record_path: str
    submission_payload: dict | None = None
    workspace_path: str | None = None
    run_dir: str | None = None
    preview_manifest: tuple[dict, ...] = ()
    artifact_manifest: tuple[dict, ...] = ()
    skills_manifest: tuple[dict, ...] = ()
    model_usage_manifest: tuple[dict, ...] = ()
    summary_metrics: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pid: int | None = None
    pid_alive: bool | None = None
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    events_log_path: str | None = None
    last_heartbeat_at: datetime | None = None
    current_phase: str | None = None
    current_step: str | None = None


class AgentSkillOSExecutionService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        bridge: AgentSkillOSBridge | None = None,
        launcher=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._bridge = bridge or AgentSkillOSBridge(settings=self._settings)
        self._launcher = launcher or self._launch_background_process

    def submit_task(
        self,
        *,
        external_order_id: str,
        prompt: str,
        input_files: tuple[str, ...] = (),
        execution_strategy: ExecutionStrategy = ExecutionStrategy.QUALITY,
        selected_plan_index: int | None = None,
    ) -> ExecutionRunSnapshot:
        repo_root = self._bridge.resolve_repo_root()
        if repo_root is None:
            raise RuntimeError("agentskillos_repo_root_not_found")
        python_executable = self._bridge.resolve_python_executable(repo_root)
        if python_executable is None:
            raise RuntimeError("agentskillos_python_executable_not_found")

        run_id = f"aso-run-{uuid4().hex[:16]}"
        service_run_dir = self._output_root() / run_id
        record_path = service_run_dir / "run.json"
        agentskillos_runs_root = service_run_dir / "agentskillos-runs"
        service_run_dir.mkdir(parents=True, exist_ok=True)
        submission_payload = {
            "intent": prompt,
            "files": list(input_files),
            "execution_strategy": execution_strategy.value,
        }
        if selected_plan_index is not None:
            submission_payload["selected_plan_index"] = selected_plan_index
        initial_payload = {
            "run_id": run_id,
            "external_order_id": external_order_id,
            "status": ExecutionRunStatus.QUEUED.value,
            "record_path": str(record_path),
            "submission_payload": submission_payload,
            "workspace_path": None,
            "run_dir": None,
            "preview_manifest": [],
            "artifact_manifest": [],
            "skills_manifest": [],
            "model_usage_manifest": [],
            "summary_metrics": {},
            "error": None,
            "started_at": None,
            "finished_at": None,
            "created_at": _utc_now().isoformat(),
            "pid": None,
            "stdout_log_path": str(service_run_dir / "stdout.log"),
            "stderr_log_path": str(service_run_dir / "stderr.log"),
            "events_log_path": str(service_run_dir / "events.ndjson"),
            "last_heartbeat_at": None,
            "current_phase": "queued",
            "current_step": None,
        }
        record_path.write_text(json.dumps(initial_payload, indent=2), encoding="utf-8")

        command = [
            str(python_executable),
            "-c",
            _EXECUTION_SCRIPT,
            str(repo_root),
            str(record_path),
            str(agentskillos_runs_root),
            external_order_id,
            prompt,
            self._settings.agentskillos_execution_mode,
            self._settings.agentskillos_skill_group,
            json.dumps(list(input_files)),
            execution_strategy.value,
            str(selected_plan_index if selected_plan_index is not None else -1),
        ]
        process_id = self._launcher(
            command,
            cwd=str(repo_root),
            env={
                **self._bridge.build_execution_env(),
                "OUTCOMEX_EXECUTION_STRATEGY": execution_strategy.value,
            },
        )
        latest_payload = json.loads(record_path.read_text(encoding="utf-8"))
        latest_payload["pid"] = process_id
        record_path.write_text(json.dumps(latest_payload, indent=2), encoding="utf-8")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> ExecutionRunSnapshot:
        record_path = self._output_root() / run_id / "run.json"
        if not record_path.exists():
            raise FileNotFoundError(run_id)
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload = self._reconcile_stale_process(payload, record_path=record_path)
        pid = payload.get("pid")
        pid_alive = self._process_exists(int(pid)) if pid else None
        return ExecutionRunSnapshot(
            run_id=payload["run_id"],
            external_order_id=payload["external_order_id"],
            status=ExecutionRunStatus(payload["status"]),
            record_path=str(record_path),
            submission_payload=payload.get("submission_payload"),
            workspace_path=payload.get("workspace_path"),
            run_dir=payload.get("run_dir"),
            preview_manifest=tuple(payload.get("preview_manifest") or ()),
            artifact_manifest=tuple(payload.get("artifact_manifest") or ()),
            skills_manifest=tuple(payload.get("skills_manifest") or ()),
            model_usage_manifest=tuple(payload.get("model_usage_manifest") or ()),
            summary_metrics=payload.get("summary_metrics") or {},
            error=payload.get("error"),
            started_at=_parse_datetime(payload.get("started_at")),
            finished_at=_parse_datetime(payload.get("finished_at")),
            pid=pid,
            pid_alive=pid_alive,
            stdout_log_path=payload.get("stdout_log_path"),
            stderr_log_path=payload.get("stderr_log_path"),
            events_log_path=payload.get("events_log_path"),
            last_heartbeat_at=_parse_datetime(payload.get("last_heartbeat_at")),
            current_phase=payload.get("current_phase"),
            current_step=payload.get("current_step"),
        )

    def _reconcile_stale_process(self, payload: dict, *, record_path: Path) -> dict:
        status = ExecutionRunStatus(payload["status"])
        pid = payload.get("pid")
        if status not in {ExecutionRunStatus.QUEUED, ExecutionRunStatus.PLANNING, ExecutionRunStatus.RUNNING}:
            return payload
        if not pid:
            return payload
        if self._process_exists(int(pid)):
            return payload

        payload = dict(payload)
        failure_time = _utc_now().isoformat()
        payload["status"] = ExecutionRunStatus.FAILED.value
        payload["error"] = payload.get("error") or "process_exited_before_terminal_status"
        payload["finished_at"] = failure_time
        payload["last_heartbeat_at"] = failure_time
        payload["current_phase"] = "failed"
        payload["current_step"] = None
        record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    @staticmethod
    def _read_process_state(pid: int) -> str | None:
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            parts = stat_path.read_text(encoding="utf-8").split()
        except OSError:
            return None
        if len(parts) < 3:
            return None
        return parts[2]

    @staticmethod
    def _process_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return AgentSkillOSExecutionService._read_process_state(pid) != "Z"

    def cancel_run(self, run_id: str) -> ExecutionRunSnapshot:
        snapshot = self.get_run(run_id)
        if snapshot.pid and snapshot.status not in {ExecutionRunStatus.SUCCEEDED, ExecutionRunStatus.FAILED, ExecutionRunStatus.CANCELLED}:
            try:
                os.kill(snapshot.pid, signal.SIGTERM)
            except OSError:
                pass
        record_path = Path(snapshot.record_path)
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload["status"] = ExecutionRunStatus.CANCELLED.value
        payload["finished_at"] = _utc_now().isoformat()
        record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.get_run(run_id)

    def collect_artifacts(self, run_id: str) -> ExecutionRunSnapshot:
        return self.get_run(run_id)

    def _output_root(self) -> Path:
        root = Path(self._settings.agentskillos_execution_output_root)
        return root if root.is_absolute() else (Path.cwd() / root).resolve()

    @staticmethod
    def _launch_background_process(command: list[str], *, cwd: str, env: dict[str, str]) -> int:
        record_path = Path(command[4])
        run_dir = record_path.parent
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_handle = open(run_dir / "stdout.log", "a", encoding="utf-8")
        stderr_handle = open(run_dir / "stderr.log", "a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()
        return int(process.pid)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def get_agentskillos_execution_service() -> AgentSkillOSExecutionService:
    return AgentSkillOSExecutionService()
