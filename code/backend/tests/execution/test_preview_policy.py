from app.execution.contracts import ExecutionRecipe, ExecutionStep, MediaType, ResourceEstimate
from app.runtime.hardware_simulator import RuntimeSnapshot
from app.runtime.preview_policy import PreviewMode, PreviewPolicy


def _video_recipe() -> ExecutionRecipe:
    return ExecutionRecipe(
        recipe_id="recipe-v",
        source_intent_id="intent-v",
        prompt="Show launch teaser",
        steps=(
            ExecutionStep(
                step_id="s1",
                provider="dashscope",
                model="wan2.2-t2v-plus",
                action="generation",
                output_type=MediaType.VIDEO,
                resources=ResourceEstimate(capacity_units=6, memory_mb=6_144, expected_duration_ticks=4),
                parameters={"prompt": "Show launch teaser"},
            ),
        ),
    )


def test_preview_policy_downgrades_video_under_runtime_pressure():
    policy = PreviewPolicy(queue_pressure_threshold=0.5, memory_pressure_threshold=0.5)
    snapshot = RuntimeSnapshot(
        used_capacity_units=10,
        total_capacity_units=24,
        used_memory_mb=20_000,
        total_memory_mb=32_768,
        running_count=2,
        queued_count=5,
        max_concurrency=3,
        max_queue_depth=8,
    )

    decision = policy.decide(_video_recipe(), snapshot)[0]

    assert decision.mode == PreviewMode.VIDEO_STORYBOARD
    assert decision.reason == "runtime_pressure_downgrade"
    assert decision.metadata["frames"] == 4


def test_preview_policy_uses_standard_video_preview_when_healthy():
    policy = PreviewPolicy()
    snapshot = RuntimeSnapshot(
        used_capacity_units=3,
        total_capacity_units=24,
        used_memory_mb=2_000,
        total_memory_mb=32_768,
        running_count=1,
        queued_count=0,
        max_concurrency=3,
        max_queue_depth=8,
    )

    decision = policy.decide(_video_recipe(), snapshot)[0]
    assert decision.mode == PreviewMode.VIDEO_POSTER
    assert decision.metadata["clip_seconds"] == 5

