#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import get_config  # noqa: E402
from orchestrator.base import ExecutionResult  # noqa: E402
from workflow.models import TaskRequest  # noqa: E402
from workflow.service import run_task  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_api_key() -> str:
    for key in ("DASHSCOPE_API_KEY", "OUTCOMEX_DASHSCOPE_API_KEY"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    raise RuntimeError("missing DASHSCOPE_API_KEY / OUTCOMEX_DASHSCOPE_API_KEY")


def _parse_args() -> argparse.Namespace:
    cfg = get_config()
    parser = argparse.ArgumentParser(
        description="Run a real AgentSkillOS end-to-end task and emit a structured report.",
    )
    parser.add_argument("--task", required=True, help="User intent passed directly into AgentSkillOS")
    parser.add_argument("--file", action="append", default=[], help="Input file path(s); pass multiple times if needed")
    parser.add_argument(
        "--mode",
        default=cfg._get("orchestrator"),
        choices=["dag", "free-style", "no-skill"],
        help="Execution mode / strategy",
    )
    parser.add_argument(
        "--skill-group",
        default=cfg.skill_group,
        help="Skill group id; local runtime may fall back to skill_seeds if larger local groups are absent",
    )
    parser.add_argument("--task-name", default="Real AgentSkillOS E2E")
    parser.add_argument("--task-id", default="real_agentskillos_e2e")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument(
        "--report",
        default="",
        help="Report path; defaults to <output-dir>/e2e-report-<timestamp>.json",
    )
    return parser.parse_args()


def _existing_run_dirs(output_dir: Path) -> set[str]:
    if not output_dir.exists():
        return set()
    return {path.name for path in output_dir.iterdir() if path.is_dir()}


def _resolve_report_path(output_dir: Path, explicit: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (output_dir / f"e2e-report-{timestamp}.json").resolve()


def _detect_run_dir(output_dir: Path, previous: set[str]) -> str:
    current = _existing_run_dirs(output_dir)
    created = sorted(current - previous)
    if created:
        return str((output_dir / created[-1]).resolve())
    if current:
        latest = max((output_dir / name for name in current), key=lambda path: path.stat().st_mtime)
        return str(latest.resolve())
    return ""


def _collect_workspace_artifacts(run_dir: str, input_files: list[str]) -> list[str]:
    if not run_dir:
        return []

    workspace_dir = Path(run_dir) / "workspace"
    if not workspace_dir.exists():
        return []

    input_names = {Path(raw).name for raw in input_files}
    artifacts: list[str] = []
    for path in sorted(p for p in workspace_dir.rglob("*") if p.is_file()):
        if path.name in input_names:
            continue
        artifacts.append(str(path.relative_to(workspace_dir)))
    return artifacts


async def _execute(args: argparse.Namespace) -> tuple[ExecutionResult, dict[str, Any]]:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []
    previous_dirs = _existing_run_dirs(output_dir)

    def _on_event(event_type: str, data: dict[str, Any]) -> None:
        events.append(
            {
                "type": event_type,
                "timestamp": _utc_now(),
                "data": data,
            }
        )

    request = TaskRequest(
        task=args.task,
        mode=args.mode,
        skill_group=args.skill_group,
        files=args.file or None,
        task_name=args.task_name,
        task_id=args.task_id,
        base_dir=str(output_dir),
    )
    result = await run_task(request, on_event=_on_event)

    search_complete = next((event for event in events if event["type"] == "search_complete"), None)
    run_dir = result.metadata.get("run_dir") or _detect_run_dir(output_dir, previous_dirs)
    artifacts = list(result.artifacts or [])
    if not artifacts:
        artifacts = _collect_workspace_artifacts(run_dir, list(args.file or []))

    report = {
        "generated_at": _utc_now(),
        "task": args.task,
        "task_name": args.task_name,
        "task_id": args.task_id,
        "mode": args.mode,
        "skill_group": args.skill_group,
        "files": list(args.file or []),
        "status": result.status,
        "summary": result.summary,
        "error": result.error,
        "artifacts": artifacts,
        "required_skills": list((search_complete or {}).get("data", {}).get("required_skills", [])),
        "skills": list((search_complete or {}).get("data", {}).get("skills", [])),
        "events": events,
        "run_dir": run_dir,
        "metadata": result.metadata,
    }
    return result, report


def main() -> int:
    args = _parse_args()
    try:
        _require_api_key()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).expanduser().resolve()
    report_path = _resolve_report_path(output_dir, args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result, report = asyncio.run(_execute(args))
    except Exception as exc:
        failure_report = {
            "generated_at": _utc_now(),
            "task": args.task,
            "task_name": args.task_name,
            "task_id": args.task_id,
            "mode": args.mode,
            "skill_group": args.skill_group,
            "files": list(args.file or []),
            "status": "failed",
            "summary": "",
            "error": str(exc),
            "artifacts": [],
            "required_skills": [],
            "skills": [],
            "events": [],
            "run_dir": "",
            "metadata": {},
        }
        report_path.write_text(json.dumps(failure_report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FAILED: {exc}")
        print(f"Report: {report_path}")
        return 1

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Status: {result.status}")
    print(f"Report: {report_path}")
    if report["run_dir"]:
        print(f"Run dir: {report['run_dir']}")
    if report["skills"]:
        print(f"Skills: {', '.join(report['skills'])}")
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
