"""Preview policy layer for text/image/video metadata decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..execution.contracts import ExecutionRecipe, MediaType
from .hardware_simulator import RuntimeSnapshot


class PreviewMode(str, Enum):
    """How a preview should be generated for a step output."""

    NONE = "none"
    TEXT_SNIPPET = "text_snippet"
    IMAGE_THUMBNAIL = "image_thumbnail"
    VIDEO_POSTER = "video_poster"
    VIDEO_STORYBOARD = "video_storyboard"


@dataclass(frozen=True)
class PreviewDecision:
    """Policy decision for one step."""

    step_id: str
    output_type: MediaType
    mode: PreviewMode
    reason: str
    metadata: dict[str, str | int | float] = field(default_factory=dict)


class PreviewPolicy:
    """Heuristic preview policy with queue/memory pressure awareness."""

    def __init__(
        self,
        queue_pressure_threshold: float = 0.6,
        memory_pressure_threshold: float = 0.75,
    ):
        self.queue_pressure_threshold = queue_pressure_threshold
        self.memory_pressure_threshold = memory_pressure_threshold

    def decide(self, recipe: ExecutionRecipe, snapshot: RuntimeSnapshot) -> tuple[PreviewDecision, ...]:
        high_pressure = (
            snapshot.queue_utilization >= self.queue_pressure_threshold
            or snapshot.memory_utilization >= self.memory_pressure_threshold
        )

        decisions: list[PreviewDecision] = []
        for step in recipe.steps:
            if step.output_type == MediaType.TEXT:
                decisions.append(
                    PreviewDecision(
                        step_id=step.step_id,
                        output_type=step.output_type,
                        mode=PreviewMode.TEXT_SNIPPET,
                        reason="text_is_low_cost",
                        metadata={"max_chars": 280},
                    )
                )
                continue

            if step.output_type == MediaType.IMAGE:
                decisions.append(
                    PreviewDecision(
                        step_id=step.step_id,
                        output_type=step.output_type,
                        mode=PreviewMode.IMAGE_THUMBNAIL,
                        reason="memory_guarded_thumbnail" if high_pressure else "standard_thumbnail",
                        metadata={"target_width": 512 if high_pressure else 768},
                    )
                )
                continue

            if step.output_type == MediaType.VIDEO:
                if high_pressure:
                    decisions.append(
                        PreviewDecision(
                            step_id=step.step_id,
                            output_type=step.output_type,
                            mode=PreviewMode.VIDEO_STORYBOARD,
                            reason="runtime_pressure_downgrade",
                            metadata={"frames": 4},
                        )
                    )
                else:
                    decisions.append(
                        PreviewDecision(
                            step_id=step.step_id,
                            output_type=step.output_type,
                            mode=PreviewMode.VIDEO_POSTER,
                            reason="default_video_preview",
                            metadata={"clip_seconds": 5},
                        )
                    )
                continue

            decisions.append(
                PreviewDecision(
                    step_id=step.step_id,
                    output_type=step.output_type,
                    mode=PreviewMode.NONE,
                    reason="unsupported_media_type",
                )
            )

        return tuple(decisions)

