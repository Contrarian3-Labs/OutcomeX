from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _load_script():
    path = PROJECT_ROOT / "scripts" / "run_real_agentskillos_e2e.py"
    spec = importlib.util.spec_from_file_location("run_real_agentskillos_e2e", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_writes_structured_report(tmp_path, monkeypatch) -> None:
    module = _load_script()

    input_image = tmp_path / "fusion_design_1.png"
    input_image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    fake_run_dir = tmp_path / "runs" / "fake-run"
    fake_run_dir.mkdir(parents=True)
    (fake_run_dir / "workspace").mkdir(parents=True)
    (fake_run_dir / "workspace" / "delivery.mp4").write_bytes(b"video")

    async def _fake_run_task(request, on_event=None):
        if on_event:
            on_event("search_start", {"task": request.task})
            on_event(
                "search_complete",
                {
                    "required_skills": ["wan-r2v-dashscope"],
                    "skills": ["wan-r2v-dashscope", "media-processing"],
                },
            )
            on_event("execution_complete", {"status": "completed", "error": None})
        return module.ExecutionResult(
            status="completed",
            summary="completed in fake runner",
            artifacts=["delivery.mp4"],
            metadata={"run_dir": str(fake_run_dir)},
        )

    monkeypatch.setattr(module, "run_task", _fake_run_task)
    monkeypatch.setattr(module, "_require_api_key", lambda: "fake-key")

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_agentskillos_e2e.py",
            "--task",
            "Use this reference image to create a short teaser video with strong consistency",
            "--file",
            str(input_image),
            "--report",
            str(report_path),
            "--output-dir",
            str(tmp_path / "runs"),
        ],
    )

    assert module.main() == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["required_skills"] == ["wan-r2v-dashscope"]
    assert payload["skills"] == ["wan-r2v-dashscope", "media-processing"]
    assert payload["artifacts"] == ["delivery.mp4"]
    assert payload["run_dir"] == str(fake_run_dir)
    assert payload["files"] == [str(input_image)]


def test_script_discovers_workspace_artifacts_when_result_is_empty(tmp_path, monkeypatch) -> None:
    module = _load_script()

    input_image = tmp_path / "fusion_design_1.png"
    input_image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    fake_run_dir = tmp_path / "runs" / "fake-run"
    workspace_dir = fake_run_dir / "workspace"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "teaser.mp4").write_bytes(b"video")
    (workspace_dir / "fusion_design_1.png").write_bytes(b"image")

    async def _fake_run_task(request, on_event=None):
        if on_event:
            on_event(
                "search_complete",
                {
                    "required_skills": ["wan-r2v-dashscope"],
                    "skills": ["wan-r2v-dashscope"],
                },
            )
        return module.ExecutionResult(
            status="completed",
            summary="completed in fake runner",
            artifacts=[],
            metadata={"run_dir": str(fake_run_dir)},
        )

    monkeypatch.setattr(module, "run_task", _fake_run_task)
    monkeypatch.setattr(module, "_require_api_key", lambda: "fake-key")

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_agentskillos_e2e.py",
            "--task",
            "Use this reference image to create a short teaser video with strong consistency",
            "--file",
            str(input_image),
            "--report",
            str(report_path),
            "--output-dir",
            str(tmp_path / "runs"),
        ],
    )

    assert module.main() == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["artifacts"] == ["teaser.mp4"]
