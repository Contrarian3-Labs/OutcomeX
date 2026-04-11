from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orchestrator.base import ExecutionResult  # noqa: E402
from workflow.models import TaskRequest  # noqa: E402
import workflow.service as workflow_service  # noqa: E402


class _FakeEngine:
    def __init__(self, *, run_context, skill_dir, allowed_tools=None) -> None:
        self.run_context = run_context
        self.skill_dir = Path(skill_dir)
        self.allowed_tools = allowed_tools

    async def run(self, request):
        self.run_context.setup(request.skills, self.skill_dir)
        copied = self.run_context.copy_files(request.files or [])

        artifact_path = self.run_context.workspace_dir / "delivery.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "task": request.task,
                    "skills": request.skills,
                    "copied_files": copied,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.run_context.finalize()

        return ExecutionResult(
            status="completed",
            summary="fake end-to-end delivery completed",
            artifacts=[str(artifact_path.name)],
            metadata={"copied_files": copied},
        )


def test_workflow_e2e_uses_anchor_and_discovered_skills(tmp_path, monkeypatch) -> None:
    input_image = tmp_path / "reference.png"
    input_image.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    output_root = tmp_path / "runs"
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        workflow_service,
        "discover_skills",
        lambda task_description, skill_group="skill_seeds", event_callback=None: ["media-processing"],
    )
    monkeypatch.setattr(
        workflow_service,
        "create_engine",
        lambda mode, run_context, skill_dir, allowed_tools=None: _FakeEngine(
            run_context=run_context,
            skill_dir=skill_dir,
            allowed_tools=allowed_tools,
        ),
    )

    request = TaskRequest(
        task="Use this reference image to create a short teaser video with strong consistency",
        mode="dag",
        skill_group="skill_1000",
        files=[str(input_image)],
        base_dir=str(output_root),
        task_id="anchor_e2e",
        task_name="Anchor E2E",
    )

    result = asyncio.run(
        workflow_service.run_task(
            request,
            on_event=lambda event_type, data: events.append((event_type, data)),
        )
    )

    assert result.status == "completed"
    assert result.artifacts == ["delivery.json"]

    search_complete = next(data for event_type, data in events if event_type == "search_complete")
    assert search_complete["required_skills"] == ["wan-r2v-dashscope"]
    assert search_complete["skills"] == ["wan-r2v-dashscope", "media-processing"]

    run_dirs = list(output_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    delivery_payload = json.loads((run_dir / "workspace" / "delivery.json").read_text(encoding="utf-8"))
    assert delivery_payload["skills"] == ["wan-r2v-dashscope", "media-processing"]
    assert delivery_payload["copied_files"] == ["reference.png"]

    assert (run_dir / ".claude" / "skills" / "wan-r2v-dashscope" / "SKILL.md").exists()
    assert (run_dir / ".claude" / "skills" / "media-processing" / "SKILL.md").exists()
