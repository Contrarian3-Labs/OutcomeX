from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.domain import benchmark_solutions as catalog_module


def test_list_benchmark_solutions_uses_resolved_vendored_root_when_setting_is_blank(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / "agentskillos"
    tasks_dir = repo_root / "benchmark" / "AgentSkillOS_bench" / "tasks"
    task_data_dir = repo_root / "benchmark" / "AgentSkillOS_bench" / "task_data"
    tasks_dir.mkdir(parents=True)
    task_data_dir.mkdir(parents=True)

    for spec in catalog_module.CURATED_SOLUTION_SPECS:
        (tasks_dir / f"{spec.task_id}.json").write_text(
            json.dumps(
                {
                    "category": "demo",
                    "task_name": spec.task_id,
                    "description": f"Description for {spec.task_id}",
                    "prompt": f"Prompt for {spec.task_id}",
                    "outputs": ["report.docx"],
                    "skills": ["demo_skill"],
                }
            ),
            encoding="utf-8",
        )
        per_task_data = task_data_dir / spec.task_id
        per_task_data.mkdir(parents=True, exist_ok=True)
        (per_task_data / "brief.txt").write_text("brief", encoding="utf-8")

    monkeypatch.setattr(
        catalog_module,
        "get_settings",
        lambda: Settings(dashscope_api_key="test-key", agentskillos_root=""),
    )
    monkeypatch.setattr(catalog_module, "resolve_agentskillos_repo_root", lambda settings: repo_root)
    catalog_module.reset_benchmark_solution_cache()

    solutions = catalog_module.list_benchmark_solutions()

    assert len(solutions) == len(catalog_module.CURATED_SOLUTION_SPECS)
    assert solutions[0].input_files == ("brief.txt",)
    assert solutions[0].outputs == ("report.docx",)
    assert solutions[0].skills == ("demo_skill",)


def test_motion_video_task4_prefers_simplicity_native_plan(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / "agentskillos"
    tasks_dir = repo_root / "benchmark" / "AgentSkillOS_bench" / "tasks"
    task_data_dir = repo_root / "benchmark" / "AgentSkillOS_bench" / "task_data"
    tasks_dir.mkdir(parents=True)
    task_data_dir.mkdir(parents=True)

    for spec in catalog_module.CURATED_SOLUTION_SPECS:
        (tasks_dir / f"{spec.task_id}.json").write_text(
            json.dumps(
                {
                    "category": "demo",
                    "task_name": spec.task_id,
                    "description": f"Description for {spec.task_id}",
                    "prompt": f"Prompt for {spec.task_id}",
                    "outputs": ["report.docx"],
                    "skills": ["demo_skill"],
                }
            ),
            encoding="utf-8",
        )
        per_task_data = task_data_dir / spec.task_id
        per_task_data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        catalog_module,
        "get_settings",
        lambda: Settings(dashscope_api_key="test-key", agentskillos_root=""),
    )
    monkeypatch.setattr(catalog_module, "resolve_agentskillos_repo_root", lambda settings: repo_root)
    catalog_module.reset_benchmark_solution_cache()

    solution = catalog_module.get_benchmark_solution("motion_video_task4")

    assert solution is not None
    assert solution.preferred_native_plan_index == 2
    assert solution.preferred_execution_strategy.value == "simplicity"
