"""Anchor-skill inference and merge helpers for workflow execution.

This module keeps capability anchoring inside AgentSkillOS so upstream callers
only need to pass task intent and files. Workflow orchestration can then merge
required anchor skills with discovered auxiliary skills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

_IMAGE_GENERATION_VERBS = {
    "generate",
    "create",
    "make",
    "design",
    "draw",
    "illustrate",
    "render",
}
_IMAGE_GENERATION_NOUNS = {
    "image",
    "poster",
    "illustration",
    "banner",
    "hero image",
    "concept art",
    "artwork",
    "visual asset",
    "photo",
    "text-to-image",
    "text to image",
}
_EDIT_WORDS = {
    "edit",
    "revise",
    "modify",
    "change",
    "retouch",
    "cleanup",
    "clean up",
    "rework",
    "cut",
}
_VIDEO_WORDS = {"video", "teaser", "trailer", "animate", "animation", "motion"}
_REFERENCE_WORDS = {"reference", "consistent", "consistency", "identity", "character"}


@dataclass(frozen=True)
class TaskAnchorIntent:
    task: str
    files: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)


def merge_skills(*, required_skills: list[str], discovered_skills: list[str]) -> list[str]:
    merged: list[str] = []
    for skill_id in [*required_skills, *discovered_skills]:
        if skill_id and skill_id not in merged:
            merged.append(skill_id)
    return merged


def infer_required_skills(intent: TaskAnchorIntent) -> list[str]:
    if intent.required_skills:
        return list(intent.required_skills)

    task = intent.task.lower()
    file_kinds = {_classify_file_kind(path) for path in intent.files}
    has_image = "image" in file_kinds
    has_video = "video" in file_kinds

    if has_video and _contains_any(task, _EDIT_WORDS | {"teaser", "cinematic"}):
        return ["wan-videoedit-dashscope"]

    if has_image and _contains_any(task, _VIDEO_WORDS):
        if _contains_any(task, _REFERENCE_WORDS):
            return ["wan-r2v-dashscope"]
        return ["wan-i2v-dashscope"]

    if has_image and _contains_any(task, _EDIT_WORDS):
        return ["image-edit-dashscope"]

    if (
        not intent.files
        and _contains_any(task, _IMAGE_GENERATION_VERBS)
        and _contains_any(task, _IMAGE_GENERATION_NOUNS)
    ):
        return ["generate-image"]

    return []


def _contains_any(text: str, candidates: set[str]) -> bool:
    return any(candidate in text for candidate in candidates)


def _classify_file_kind(raw_path: str) -> str:
    suffix = Path(raw_path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    return "other"
