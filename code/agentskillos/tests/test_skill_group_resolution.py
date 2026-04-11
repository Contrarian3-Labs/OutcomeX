import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from constants import resolve_skill_group  # noqa: E402


def test_skill_1000_falls_back_to_existing_local_paths() -> None:
    group = resolve_skill_group("skill_1000")

    assert Path(group["skills_dir"]).exists()
    assert Path(group["tree_path"]).exists()
    assert group["fallback_group_id"] == "skill_seeds"
    assert "skills_dir" in group["patched_paths"]


def test_default_group_resolves_without_patch() -> None:
    group = resolve_skill_group("skill_seeds")

    assert Path(group["skills_dir"]).exists()
    assert Path(group["tree_path"]).exists()
    assert "patched_paths" not in group
