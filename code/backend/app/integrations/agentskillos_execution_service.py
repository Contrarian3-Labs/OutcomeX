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

sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import config
config.Config.reset()
from constants import resolve_skill_group
from workflow.models import TaskRequest
from workflow.service import run_task


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_record(payload):
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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


async def main():
    initial = json.loads(record_path.read_text(encoding="utf-8"))
    initial["status"] = "running"
    initial["started_at"] = utc_now()
    write_record(initial)

    before_dirs = {p.name for p in base_dir.iterdir()} if base_dir.exists() else set()
    base_dir.mkdir(parents=True, exist_ok=True)

    request = TaskRequest(
        task=task_prompt,
        mode=mode,
        skill_group=skill_group,
        files=files or None,
        base_dir=str(base_dir),
        task_id=external_order_id,
    )

    try:
        await run_task(request)
    except Exception as exc:
        failed = dict(initial)
        failed["status"] = "failed"
        failed["error"] = f"{exc.__class__.__name__}: {exc}"
        failed["finished_at"] = utc_now()
        write_record(failed)
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
        for skill_id in meta.get("skills", [])
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

    final = dict(initial)
    final.update(
        {
            "status": "succeeded" if result.get("status") == "completed" else "failed",
            "run_dir": str(run_dir),
            "workspace_path": str(workspace_dir),
            "artifact_manifest": artifacts,
            "preview_manifest": choose_preview(artifacts),
            "skills_manifest": skills,
            "model_usage_manifest": model_usage,
            "summary_metrics": summary_metrics,
            "error": result.get("error"),
            "finished_at": utc_now(),
        }
    )
    write_record(final)


asyncio.run(main())
""".strip()


@dataclass(frozen=True)
class ExecutionRunSnapshot:
    run_id: str
    external_order_id: str
    status: ExecutionRunStatus
    record_path: str
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
        initial_payload = {
            "run_id": run_id,
            "external_order_id": external_order_id,
            "status": ExecutionRunStatus.QUEUED.value,
            "record_path": str(record_path),
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
        ]
        process_id = self._launcher(
            command,
            cwd=str(repo_root),
            env=self._bridge.build_execution_env(),
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
        return ExecutionRunSnapshot(
            run_id=payload["run_id"],
            external_order_id=payload["external_order_id"],
            status=ExecutionRunStatus(payload["status"]),
            record_path=str(record_path),
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
            pid=payload.get("pid"),
        )

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
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(process.pid)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def get_agentskillos_execution_service() -> AgentSkillOSExecutionService:
    return AgentSkillOSExecutionService()
