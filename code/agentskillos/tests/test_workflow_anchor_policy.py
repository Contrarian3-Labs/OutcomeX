import sys
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_SEEDS_DIR = PROJECT_ROOT / "data" / "skill_seeds"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

ANCHOR_POLICY_PATH = PROJECT_ROOT / "src" / "workflow" / "anchor_policy.py"
spec = importlib.util.spec_from_file_location("anchor_policy", ANCHOR_POLICY_PATH)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

TaskAnchorIntent = module.TaskAnchorIntent
infer_required_skills = module.infer_required_skills
merge_skills = module.merge_skills


def test_infer_required_skills_for_image_generation_without_inputs() -> None:
    intent = TaskAnchorIntent(task="Generate a polished hero image for our landing page")
    required = infer_required_skills(intent)

    assert required == ["generate-image"]


def test_infer_required_skills_for_chinese_text_to_video_without_inputs() -> None:
    intent = TaskAnchorIntent(task="生成一个美女视频")

    assert infer_required_skills(intent) == ["wan-t2v-dashscope"]


def test_infer_required_skills_for_image_edit_with_image_input() -> None:
    intent = TaskAnchorIntent(
        task="Edit this product image and keep the composition realistic",
        files=["/tmp/source.png"],
    )
    required = infer_required_skills(intent)

    assert required == ["image-edit-dashscope"]


def test_infer_required_skills_for_reference_to_video() -> None:
    intent = TaskAnchorIntent(
        task="Use this reference image to create a short video teaser with strong consistency",
        files=["/tmp/reference.jpg"],
    )
    required = infer_required_skills(intent)

    assert required == ["wan-r2v-dashscope"]


def test_infer_required_skills_for_chinese_reference_to_video() -> None:
    intent = TaskAnchorIntent(
        task="基于这张参考图生成一个人物一致的视频",
        files=["/tmp/reference.jpg"],
    )

    assert infer_required_skills(intent) == ["wan-r2v-dashscope"]


def test_infer_required_skills_for_video_edit() -> None:
    intent = TaskAnchorIntent(
        task="Edit this raw video into a short cinematic teaser",
        files=["/tmp/raw.mp4"],
    )
    required = infer_required_skills(intent)

    assert required == ["wan-videoedit-dashscope"]


def test_merge_skills_keeps_required_first_and_deduplicated() -> None:
    merged = merge_skills(
        required_skills=["wan-videoedit-dashscope", "media-processing"],
        discovered_skills=["media-processing", "subtitle", "thumbnail"],
    )

    assert merged == [
        "wan-videoedit-dashscope",
        "media-processing",
        "subtitle",
        "thumbnail",
    ]


def test_task_anchor_intent_supports_required_skills_field() -> None:
    intent = TaskAnchorIntent(
        task="Create a teaser",
        required_skills=["wan-r2v-dashscope"],
    )

    assert intent.required_skills == ["wan-r2v-dashscope"]


def test_plain_text_task_keeps_anchor_empty() -> None:
    intent = TaskAnchorIntent(
        task="Summarize the meeting transcript into five concise action items",
    )

    assert infer_required_skills(intent) == []


def test_document_task_with_pdf_input_keeps_anchor_empty() -> None:
    intent = TaskAnchorIntent(
        task="Read this PDF and produce a structured executive summary with risks and next steps",
        files=["/tmp/board-memo.pdf"],
    )

    assert infer_required_skills(intent) == []


def test_cover_letter_does_not_false_positive_as_image_generation() -> None:
    intent = TaskAnchorIntent(
        task="Create a cover letter for a senior product manager application",
    )

    assert infer_required_skills(intent) == []


def test_anchor_skills_exist_in_skill_seeds() -> None:
    expected = [
        "generate-image",
        "image-edit-dashscope",
        "wan-i2v-dashscope",
        "wan-r2v-dashscope",
        "wan-t2v-dashscope",
        "wan-videoedit-dashscope",
    ]

    missing = [skill_id for skill_id in expected if not (SKILL_SEEDS_DIR / skill_id / "SKILL.md").exists()]
    assert missing == []
