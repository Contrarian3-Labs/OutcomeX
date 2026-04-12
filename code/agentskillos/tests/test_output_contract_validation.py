from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orchestrator.dag.engine import SkillOrchestrator  # noqa: E402
from orchestrator.dag.prompts import build_isolated_executor_prompt  # noqa: E402


def test_extract_expected_output_paths_reads_concrete_files() -> None:
    outputs_summary = (
        "Final video file (transformer_attention.mp4, 1920x1080, ~30s) "
        "and source code (src/video_compose.py) with all frame assembly logic."
    )

    paths = SkillOrchestrator._extract_expected_output_paths(outputs_summary)

    assert paths == ["transformer_attention.mp4", "src/video_compose.py"]


def test_find_missing_expected_outputs_handles_workspace_prefixed_paths(tmp_path) -> None:
    output_dir = tmp_path / "workspace"
    (output_dir / "src").mkdir(parents=True)
    (output_dir / "src" / "attention_animation.html").write_text("<html></html>", encoding="utf-8")

    outputs_summary = (
        "Interactive HTML file (workspace/src/attention_animation.html) "
        "and rendered preview (workspace/transformer_attention.mp4)."
    )

    engine = SkillOrchestrator.__new__(SkillOrchestrator)
    missing = engine._find_missing_expected_outputs(
        output_dir=output_dir,
        outputs_summary=outputs_summary,
    )

    assert missing == ["workspace/transformer_attention.mp4"]


def test_isolated_executor_prompt_requires_real_output_verification() -> None:
    prompt = build_isolated_executor_prompt(
        overall_task="Create a video",
        skill_name="data-visualization",
        node_purpose="Assemble the final video",
        artifacts_context="None",
        output_dir="/tmp/workspace",
        outputs_summary="transformer_attention.mp4 and src/video_compose.py",
        downstream_hint="Used by the final delivery",
        working_dir="/tmp/run",
    )

    assert "verify every concrete output file named above exists" in prompt
    assert "do not stop at writing code: run it" in prompt
