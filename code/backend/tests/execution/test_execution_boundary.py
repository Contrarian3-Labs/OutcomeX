from typing import get_type_hints

from app.execution.contracts import (
    ExecutionConstraints,
    ExecutionRecipe,
    ExecutionStep,
    IntentRequest,
    MatchStatus,
    MediaType,
    ResourceEstimate,
    SolutionMatchResult,
)
from app.execution.matcher import match_recipe_to_solution
from app.execution.normalizer import normalize_intent_to_recipe
from app.execution.service import ExecutionPlan


def test_execution_plan_boundary_uses_concrete_execution_types():
    hints = get_type_hints(ExecutionPlan)
    assert hints["recipe"] is ExecutionRecipe
    assert hints["match"] is SolutionMatchResult


def test_normalizer_limits_mvp_recipe_to_single_step():
    intent = IntentRequest(
        intent_id="intent-multi",
        prompt="Create both an image and a video",
        desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
    )

    recipe = normalize_intent_to_recipe(intent)

    assert len(recipe.steps) == 1
    assert recipe.steps[0].output_type == MediaType.IMAGE
    assert recipe.metadata["outputs"] == "image"


def test_matcher_rejects_multi_step_recipe_for_mvp():
    recipe = ExecutionRecipe(
        recipe_id="recipe-multi",
        source_intent_id="intent-multi",
        prompt="Create both",
        steps=(
            ExecutionStep(
                step_id="s1",
                provider="alibaba-mulerouter",
                model="alibaba/wan2.6-t2i",
                action="generation",
                output_type=MediaType.IMAGE,
                resources=ResourceEstimate(capacity_units=3, memory_mb=2_048, expected_duration_ticks=2),
            ),
            ExecutionStep(
                step_id="s2",
                provider="alibaba-mulerouter",
                model="alibaba/wan2.6-t2v",
                action="generation",
                output_type=MediaType.VIDEO,
                resources=ResourceEstimate(capacity_units=6, memory_mb=6_144, expected_duration_ticks=4),
            ),
        ),
    )

    result = match_recipe_to_solution(recipe, ExecutionConstraints())

    assert result.status == MatchStatus.NO_MATCH
    assert result.selected is None
    assert result.missing_requirements == ("multi_step_recipe_not_supported",)
