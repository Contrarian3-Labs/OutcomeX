"""Service wrapper that treats AgentSkillOS as the execution kernel."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.config import Settings, get_settings
from ..domain.enums import ExecutionRunStatus
from ..execution.contracts import ExecutionStrategy
from ..execution.observability import list_log_files, read_events_after_seq, resolve_logs_root_path
from .agentskillos_bridge import AgentSkillOSBridge

_STALL_AFTER_SECONDS = 60
_IGNORED_ARTIFACT_PARTS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".npm",
        ".yarn",
        ".pnpm-store",
        ".turbo",
    }
)

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
    seq = int(initial.get("event_cursor", 0) or 0) + 1
    timestamp = utc_now()
    initial["event_cursor"] = seq
    initial["last_progress_at"] = timestamp
    if initial.get("started_at") is not None:
        write_record(initial)
    events_path = Path(initial.get("events_log_path") or (record_path.parent / "events.ndjson"))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "seq": seq,
        "timestamp": timestamp,
        "run_id": initial.get("run_id"),
        "phase": fields.pop("phase", initial.get("current_phase")),
        "event": event_type,
        "level": fields.pop("level", "info"),
        "message": fields.pop("message", event_type),
        "data": fields,
    }
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\\n")


def heartbeat(payload, *, phase, step=None):
    payload["last_heartbeat_at"] = utc_now()
    payload["current_phase"] = phase
    payload["current_step"] = step
    write_record(payload)
    append_event("heartbeat", phase=phase, step=step, message=f"Heartbeat: {phase}")


def normalize_plan_candidate(plan, index):
    if not isinstance(plan, dict):
        return {
            "index": index,
            "name": f"Plan {index + 1}",
            "description": "",
            "strategy": execution_strategy,
        }
    return {
        "index": index,
        "name": str(plan.get("name") or f"Plan {index + 1}"),
        "description": str(plan.get("description") or ""),
        "strategy": str(plan.get("strategy") or execution_strategy or ""),
    }


def build_dag_payload(nodes):
    dag_nodes = []
    edges = []
    for node in list(nodes or []):
        node_id = str(node.get("id") or node.get("name") or f"node-{len(dag_nodes) + 1}")
        dag_nodes.append(
            {
                "id": node_id,
                "name": str(node.get("name") or node_id),
                "status": "pending",
            }
        )
        for dependency in node.get("depends_on") or []:
            edges.append({"from": str(dependency), "to": node_id})
    return {"nodes": dag_nodes, "edges": edges}


def update_dag_status(node_id, status):
    dag = initial.get("dag") or {}
    nodes = list(dag.get("nodes") or [])
    mapped = {"completed": "succeeded"}.get(status, status)
    for node in nodes:
        if node.get("id") == node_id:
            node["status"] = mapped
            break
    dag["nodes"] = nodes
    initial["dag"] = dag
    initial["active_node_id"] = node_id if mapped == "running" else None
    write_record(initial)
    event_type = "dag_node_started" if mapped == "running" else "dag_node_finished"
    append_event(
        event_type,
        phase=initial.get("current_phase"),
        message=f"Node {node_id} -> {mapped}",
        node_id=node_id,
        status=mapped,
    )


class StreamMirror:
    def __init__(self, *, stream_name, wrapped, level):
        self.stream_name = stream_name
        self.wrapped = wrapped
        self.level = level
        self.buffer = ""

    def write(self, chunk):
        if not chunk:
            return 0
        written = self.wrapped.write(chunk)
        self.wrapped.flush()
        self.buffer += chunk
        while "\\n" in self.buffer:
            line, self.buffer = self.buffer.split("\\n", 1)
            line = line.rstrip("\\r")
            if line:
                append_event(
                    f"{self.stream_name}_line",
                    phase=initial.get("current_phase"),
                    level=self.level,
                    message=line,
                    stream=self.stream_name,
                )
        return written if written is not None else len(chunk)

    def flush(self):
        self.wrapped.flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self.wrapped, "encoding", "utf-8")


class OutcomeXTelemetryVisualizer:
    def __init__(self, auto_select_plan):
        self.inner = NullVisualizer(auto_select_plan=auto_select_plan)

    async def start(self):
        await self.inner.start()

    async def stop(self):
        await self.inner.stop()

    async def set_task(self, task):
        await self.inner.set_task(task)

    async def set_nodes(self, nodes, phases):
        await self.inner.set_nodes(nodes, phases)
        initial["dag"] = build_dag_payload(nodes)
        initial["active_node_id"] = None
        write_record(initial)
        append_event(
            "dag_initialized",
            phase=initial.get("current_phase"),
            message=f"Initialized DAG with {len(list(nodes or []))} nodes",
            dag=initial["dag"],
        )

    async def update_status(self, node_id, status):
        await self.inner.update_status(node_id, status)
        update_dag_status(node_id, status)

    async def set_phase(self, phase_num):
        await self.inner.set_phase(phase_num)
        initial["current_step"] = f"phase-{phase_num}"
        write_record(initial)

    async def add_log(self, message, level="info", node_id=None):
        await self.inner.add_log(message, level, node_id=node_id)

    def add_metrics(self, input_tokens, output_tokens, cost):
        self.inner.add_metrics(input_tokens, output_tokens, cost)

    async def select_plan(self, plans):
        plan_candidates = [normalize_plan_candidate(plan, idx) for idx, plan in enumerate(list(plans or []))]
        initial["plan_candidates"] = plan_candidates
        write_record(initial)
        append_event(
            "plan_candidates_generated",
            phase="plan_generation",
            message=f"Generated {len(plan_candidates)} candidate plans",
            plans=plan_candidates,
        )
        selected_index = await self.inner.select_plan(plans)
        selected_plan = list(plans or [])[selected_index] if plans else None
        initial["selected_plan"] = selected_plan
        write_record(initial)
        append_event(
            "plan_selected",
            phase="plan_selection",
            message="Selected execution plan",
            selected_plan_index=selected_index,
            selected_plan=selected_plan,
        )
        return selected_index

    async def set_workflow_phase(self, phase):
        await self.inner.set_workflow_phase(phase)
        initial["current_phase"] = phase
        initial["current_step"] = None
        write_record(initial)


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


def build_log_entry(kind, path):
    try:
        stat_result = path.stat()
        size = int(stat_result.st_size)
        updated_at = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        size = 0
        updated_at = None
    return {
        "kind": kind,
        "name": path.name,
        "path": str(path),
        "size": size,
        "updated_at": updated_at,
    }


def discover_log_sources(run_dir):
    files = []
    seen = set()
    logs_root = run_dir / "logs"
    if logs_root.exists() and logs_root.is_dir():
        for candidate in sorted(logs_root.iterdir(), key=lambda item: item.name):
            if not candidate.is_file():
                continue
            files.append(build_log_entry("raw_file", candidate))
            seen.add(candidate.resolve())
    for kind, key in (("stdout", "stdout_log_path"), ("stderr", "stderr_log_path")):
        path_value = initial.get(key)
        if not path_value:
            continue
        candidate = Path(path_value)
        if not candidate.exists() or not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        files.append(build_log_entry(kind, candidate))
        seen.add(resolved)
    return (str(logs_root) if logs_root.exists() and logs_root.is_dir() else None), files


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
    append_event(
        "anchor_inferred",
        phase="anchor_inference",
        message=f"Resolved {len(required_skills)} anchor skills",
        required_skills=required_skills,
    )
    discovered_skills = discover_skills(task_prompt, skill_group=skill_group)
    skills = merge_skills(required_skills=required_skills, discovered_skills=discovered_skills)
    append_event(
        "skills_discovered",
        phase="skill_discovery",
        message=f"Discovered {len(skills)} skills",
        skills=skills,
    )
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
    visualizer = OutcomeXTelemetryVisualizer(
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
    sys.stdout = StreamMirror(stream_name="stdout", wrapped=sys.stdout, level="info")
    sys.stderr = StreamMirror(stream_name="stderr", wrapped=sys.stderr, level="warning")
    initial["status"] = "running"
    initial["started_at"] = utc_now()
    initial.setdefault("event_cursor", 0)
    initial.setdefault("plan_candidates", [])
    initial.setdefault("dag", None)
    initial.setdefault("active_node_id", None)
    initial.setdefault("logs_root_path", None)
    initial.setdefault("log_files", [])
    initial.setdefault("last_progress_at", initial["started_at"])
    initial.setdefault("stalled", False)
    initial.setdefault("stalled_reason", None)
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
        initial.update(failed)
        write_record(initial)
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
            if any(part in {
                "node_modules",
                "__pycache__",
                ".git",
                ".venv",
                "venv",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".npm",
                ".yarn",
                ".pnpm-store",
                ".turbo",
            } for part in Path(rel).parts):
                continue
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

    logs_root_path, log_files = discover_log_sources(run_dir)

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
            "last_progress_at": utc_now(),
            "current_phase": "finished" if result.get("status") == "completed" else "failed",
            "current_step": None,
            "logs_root_path": logs_root_path,
            "log_files": log_files,
            "stalled": False,
            "stalled_reason": None,
        }
    )
    if selected_plan is not None:
        final["selected_plan"] = selected_plan
    initial.update(final)
    write_record(initial)
    for artifact in initial.get("artifact_manifest") or []:
        append_event(
            "artifact_created",
            phase="artifact_collection",
            message=f"Collected artifact {artifact.get('path')}",
            artifact=artifact,
        )
    for preview in initial.get("preview_manifest") or []:
        append_event(
            "preview_created",
            phase="preview_ready",
            message=f"Preview ready {preview.get('path')}",
            preview=preview,
        )
    append_event("run_finished", status=initial["status"], error=initial.get("error"))


asyncio.run(main())
""".strip()



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



def is_visible_artifact_path(path_value: str | Path) -> bool:
    parts = Path(path_value).parts
    return all(part not in _IGNORED_ARTIFACT_PARTS for part in parts)



def choose_preview(artifacts: list[dict]) -> list[dict]:
    preferred = {"image", "video", "html", "presentation", "document"}
    return [artifact for artifact in artifacts if artifact.get("type") in preferred][:5]



def collect_visible_artifacts(*, run_dir: Path, workspace_dir: Path) -> list[dict]:
    artifacts: list[dict] = []
    if not workspace_dir.exists():
        return artifacts
    for path in sorted(p for p in workspace_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(run_dir).as_posix()
        if not is_visible_artifact_path(rel):
            continue
        artifacts.append(
            {
                "path": rel,
                "type": classify_artifact(path),
                "role": "final",
            }
        )
    return artifacts



def sanitize_visible_manifests(payload: dict) -> dict:
    original_artifacts = list(payload.get("artifact_manifest") or [])
    original_previews = list(payload.get("preview_manifest") or [])
    artifact_manifest = [
        dict(item)
        for item in original_artifacts
        if is_visible_artifact_path(str(item.get("path") or ""))
    ]
    visible_artifact_paths = {str(item.get("path") or "") for item in artifact_manifest}
    preview_manifest = [
        dict(item)
        for item in original_previews
        if str(item.get("path") or "") in visible_artifact_paths and is_visible_artifact_path(str(item.get("path") or ""))
    ]
    if not preview_manifest or len(preview_manifest) != len(original_previews):
        preview_manifest = choose_preview(artifact_manifest)

    if artifact_manifest == original_artifacts and preview_manifest == original_previews:
        return payload

    sanitized = dict(payload)
    sanitized["artifact_manifest"] = artifact_manifest
    sanitized["preview_manifest"] = preview_manifest
    return sanitized


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
    selected_plan: dict | None = None
    plan_candidates: tuple[dict, ...] = ()
    dag: dict | None = None
    active_node_id: str | None = None
    logs_root_path: str | None = None
    log_files: tuple[dict, ...] = ()
    event_cursor: int = 0
    last_progress_at: datetime | None = None
    stalled: bool = False
    stalled_reason: str | None = None


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
        staged_input_files = self._stage_input_files(
            service_run_dir=service_run_dir,
            input_files=input_files,
        )
        submission_payload = {
            "intent": prompt,
            "files": list(staged_input_files),
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
            "selected_plan": None,
            "plan_candidates": [],
            "dag": None,
            "active_node_id": None,
            "logs_root_path": None,
            "log_files": [],
            "event_cursor": 0,
            "last_progress_at": None,
            "stalled": False,
            "stalled_reason": None,
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
            json.dumps(list(staged_input_files)),
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

    @staticmethod
    def _stage_input_files(*, service_run_dir: Path, input_files: tuple[str, ...]) -> tuple[str, ...]:
        staged_files: list[str] = []
        inputs_root = service_run_dir / "inputs"
        for index, input_file in enumerate(input_files):
            raw_value = str(input_file)
            candidate = Path(raw_value)
            if candidate.exists() and candidate.is_file():
                inputs_root.mkdir(parents=True, exist_ok=True)
                destination = inputs_root / f"{index:02d}-{candidate.name or 'attachment.bin'}"
                shutil.copy2(candidate, destination)
                staged_files.append(str(destination))
                continue
            staged_files.append(raw_value)
        return tuple(staged_files)

    def get_run(self, run_id: str) -> ExecutionRunSnapshot:
        record_path = self._output_root() / run_id / "run.json"
        if not record_path.exists():
            raise FileNotFoundError(run_id)
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload = self._reconcile_stale_process(payload, record_path=record_path)
        sanitized_payload = sanitize_visible_manifests(payload)
        if sanitized_payload is not payload:
            payload = sanitized_payload
            record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        pid = payload.get("pid")
        pid_alive = self._process_exists(int(pid)) if pid else None
        last_heartbeat_at = _parse_datetime(payload.get("last_heartbeat_at"))
        last_progress_at = _parse_datetime(payload.get("last_progress_at")) or last_heartbeat_at
        log_files = tuple(
            payload.get("log_files")
            or list_log_files(
                run_dir=payload.get("run_dir"),
                stdout_path=payload.get("stdout_log_path"),
                stderr_path=payload.get("stderr_log_path"),
            )
        )
        logs_root_path = payload.get("logs_root_path") or resolve_logs_root_path(payload.get("run_dir"))
        event_cursor = _coerce_int(payload.get("event_cursor"), default=0)
        if event_cursor <= 0:
            event_cursor = read_events_after_seq(payload.get("events_log_path"), after_seq=0).next_cursor
        stalled, stalled_reason = _compute_stalled_state(
            status=ExecutionRunStatus(payload["status"]),
            last_progress_at=last_progress_at,
            now=_utc_now(),
        )
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
            last_heartbeat_at=last_heartbeat_at,
            current_phase=payload.get("current_phase"),
            current_step=payload.get("current_step"),
            selected_plan=payload.get("selected_plan"),
            plan_candidates=tuple(payload.get("plan_candidates") or ()),
            dag=payload.get("dag"),
            active_node_id=payload.get("active_node_id"),
            logs_root_path=logs_root_path,
            log_files=log_files,
            event_cursor=event_cursor,
            last_progress_at=last_progress_at,
            stalled=stalled,
            stalled_reason=payload.get("stalled_reason") or stalled_reason,
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
        payload["last_progress_at"] = failure_time
        payload["current_phase"] = "failed"
        payload["current_step"] = None
        payload["stalled"] = False
        payload["stalled_reason"] = None
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
        payload["last_progress_at"] = payload["finished_at"]
        payload["stalled"] = False
        payload["stalled_reason"] = None
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


def _coerce_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _compute_stalled_state(
    *,
    status: ExecutionRunStatus,
    last_progress_at: datetime | None,
    now: datetime,
) -> tuple[bool, str | None]:
    if status not in {ExecutionRunStatus.QUEUED, ExecutionRunStatus.PLANNING, ExecutionRunStatus.RUNNING}:
        return False, None
    if last_progress_at is None:
        return False, None
    if last_progress_at.tzinfo is None:
        last_progress_at = last_progress_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stalled_seconds = (now - last_progress_at).total_seconds()
    if stalled_seconds < _STALL_AFTER_SECONDS:
        return False, None
    return True, f"no_progress_for_{int(stalled_seconds)}s"


def get_agentskillos_execution_service() -> AgentSkillOSExecutionService:
    return AgentSkillOSExecutionService()
