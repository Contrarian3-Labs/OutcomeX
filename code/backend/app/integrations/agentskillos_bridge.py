"""Subprocess bridge into the local AgentSkillOS checkout.

This bridge deliberately uses a subprocess boundary so OutcomeX can invoke
AgentSkillOS planning/execution helpers without importing its generic top-level
modules (`config`, `workflow`, `manager`, etc.) into the backend process.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Settings, get_settings

_DISCOVERY_SCRIPT = """
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
task = sys.argv[2]
skill_group = sys.argv[3]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import config
config.Config.reset()
from workflow.service import discover_skills

skills = discover_skills(task, skill_group=skill_group)
print(json.dumps({"skills": skills}))
""".strip()

_PLANNING_SCRIPT = """
import asyncio
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
task = sys.argv[2]
skill_group = sys.argv[3]
files = json.loads(sys.argv[4])
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import config
config.Config.reset()
from constants import resolve_skill_group
from orchestrator.registry import create_engine
from orchestrator.visualizers import NullVisualizer
from workflow.anchor_policy import TaskAnchorIntent, infer_required_skills, merge_skills
from workflow.service import discover_skills


async def main():
    required_skills = infer_required_skills(
        TaskAnchorIntent(task=task, files=list(files or []), required_skills=[])
    )
    discovered_skills = discover_skills(task, skill_group=skill_group)
    skills = merge_skills(required_skills=required_skills, discovered_skills=discovered_skills)
    skill_group_cfg = resolve_skill_group(skill_group)
    engine = create_engine("dag", run_context=None, skill_dir=skill_group_cfg["skills_dir"], allowed_tools=None)
    result = await engine.run_with_visualizer(
        task=task,
        skill_names=skills,
        visualizer=NullVisualizer(auto_select_plan=0),
        plan_only=True,
        files=list(files or []) or None,
    )
    print(json.dumps({"skills": skills, "plans": result.metadata.get("plans", [])}, ensure_ascii=False))


asyncio.run(main())
""".strip()


@dataclass(frozen=True)
class AgentSkillOSDiscoveryResult:
    skill_ids: tuple[str, ...]
    source: str
    error: str = ""
    repo_root: str = ""


@dataclass(frozen=True)
class AgentSkillOSNativePlan:
    plan_index: int
    name: str
    description: str
    nodes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class AgentSkillOSPlanningResult:
    plans: tuple[AgentSkillOSNativePlan, ...]
    skill_ids: tuple[str, ...]
    source: str
    error: str = ""
    repo_root: str = ""


@dataclass(frozen=True)
class CompletedProcessLike:
    returncode: int
    stdout: str
    stderr: str = ""


class AgentSkillOSBridge:
    """Calls the local AgentSkillOS checkout with OutcomeX-controlled LLM env."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        runner=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._runner = runner or self._run_subprocess

    def discover_skills(self, task: str) -> AgentSkillOSDiscoveryResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled

        prepared = self._prepare_runtime()
        if isinstance(prepared, AgentSkillOSDiscoveryResult):
            return prepared
        repo_root, python_executable = prepared

        process = self._run_agent_skill_os(
            script=_DISCOVERY_SCRIPT,
            repo_root=repo_root,
            python_executable=python_executable,
            args=[task, self._settings.agentskillos_skill_group],
            timeout_seconds=self._settings.agentskillos_discovery_timeout_seconds,
        )
        if process.returncode != 0:
            error = process.stderr.strip() or process.stdout.strip() or f"returncode:{process.returncode}"
            return AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_failed",
                error=error,
                repo_root=str(repo_root),
            )

        try:
            payload = json.loads(process.stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            return AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_failed",
                error=f"invalid_json:{exc.__class__.__name__}",
                repo_root=str(repo_root),
            )

        skill_ids = tuple(str(skill_id) for skill_id in payload.get("skills", []))
        return AgentSkillOSDiscoveryResult(
            skill_ids=skill_ids,
            source="agentskillos_discovery",
            repo_root=str(repo_root),
        )

    def generate_plans(self, task: str, *, files: tuple[str, ...] = ()) -> AgentSkillOSPlanningResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return AgentSkillOSPlanningResult(
                plans=(),
                skill_ids=(),
                source=disabled.source,
                error=disabled.error,
                repo_root=disabled.repo_root,
            )

        prepared = self._prepare_runtime()
        if isinstance(prepared, AgentSkillOSDiscoveryResult):
            return AgentSkillOSPlanningResult(
                plans=(),
                skill_ids=(),
                source=prepared.source,
                error=prepared.error,
                repo_root=prepared.repo_root,
            )
        repo_root, python_executable = prepared

        process = self._run_agent_skill_os(
            script=_PLANNING_SCRIPT,
            repo_root=repo_root,
            python_executable=python_executable,
            args=[task, self._settings.agentskillos_skill_group, json.dumps(list(files))],
            timeout_seconds=self._settings.agentskillos_discovery_timeout_seconds,
        )
        if process.returncode != 0:
            error = process.stderr.strip() or process.stdout.strip() or f"returncode:{process.returncode}"
            return AgentSkillOSPlanningResult(
                plans=(),
                skill_ids=(),
                source="agentskillos_failed",
                error=error,
                repo_root=str(repo_root),
            )

        try:
            payload = json.loads(process.stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            return AgentSkillOSPlanningResult(
                plans=(),
                skill_ids=(),
                source="agentskillos_failed",
                error=f"invalid_json:{exc.__class__.__name__}",
                repo_root=str(repo_root),
            )

        raw_plans = payload.get("plans", [])
        plans = tuple(
            AgentSkillOSNativePlan(
                plan_index=index,
                name=str(plan.get("name", f"Plan {index + 1}")),
                description=str(plan.get("description", "")),
                nodes=tuple(dict(node) for node in (plan.get("nodes") or [])),
            )
            for index, plan in enumerate(raw_plans)
        )
        return AgentSkillOSPlanningResult(
            plans=plans,
            skill_ids=tuple(str(skill_id) for skill_id in payload.get("skills", [])),
            source="agentskillos_planning",
            repo_root=str(repo_root),
        )

    def _disabled_result(self) -> AgentSkillOSDiscoveryResult | None:
        if not self._settings.dashscope_api_key.strip():
            return AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_disabled",
                error="dashscope_api_key_missing",
            )
        return None

    def _prepare_runtime(self) -> tuple[Path, Path] | AgentSkillOSDiscoveryResult:
        repo_root = self.resolve_repo_root()
        if repo_root is None:
            return AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_unavailable",
                error="repo_root_not_found",
            )

        python_executable = self.resolve_python_executable(repo_root)
        if python_executable is None:
            return AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_unavailable",
                error="python_executable_not_found",
                repo_root=str(repo_root),
            )
        return repo_root, python_executable

    def _run_agent_skill_os(
        self,
        *,
        script: str,
        repo_root: Path,
        python_executable: Path,
        args: list[str],
        timeout_seconds: float,
    ) -> CompletedProcessLike:
        return self._runner(
            [str(python_executable), "-c", script, str(repo_root), *args],
            env=self.build_execution_env(),
            cwd=str(repo_root),
            timeout_seconds=timeout_seconds,
        )

    def resolve_repo_root(self) -> Path | None:
        configured = self._settings.agentskillos_root.strip()
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))

        for parent in Path(__file__).resolve().parents:
            sibling = parent.parent / "Hashkey" / "reference-code" / "AgentSkillOS"
            candidates.append(sibling)

        for candidate in candidates:
            if (candidate / "run.py").exists() and (candidate / "src" / "workflow" / "service.py").exists():
                return candidate
        return None

    @staticmethod
    def resolve_python_executable(repo_root: Path) -> Path | None:
        candidates = (
            repo_root / ".venv" / "bin" / "python",
            repo_root / ".venv" / "Scripts" / "python.exe",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def build_execution_env(self) -> dict[str, str]:
        env = os.environ.copy()
        model = self._settings.agentskillos_llm_model or f"openai/{self._settings.dashscope_text_model}"
        env.update(
            {
                "LLM_MODEL": model,
                "LLM_BASE_URL": self._settings.dashscope_compatible_base_url,
                "LLM_API_KEY": self._settings.dashscope_api_key,
                "OPENAI_BASE_URL": self._settings.dashscope_compatible_base_url,
                "OPENAI_API_KEY": self._settings.dashscope_api_key,
            }
        )
        return env

    @staticmethod
    def _run_subprocess(
        command: list[str],
        *,
        env: dict[str, str],
        cwd: str,
        timeout_seconds: float,
    ) -> CompletedProcessLike:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return CompletedProcessLike(
                returncode=124,
                stdout="",
                stderr="agentskillos_discovery_timeout",
            )
        return CompletedProcessLike(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
