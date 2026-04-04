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
from app.execution.service import ExecutionEngineService, ExecutionPlan
from app.runtime.hardware_simulator import AdmissionStatus


class _FailIfCalledProvider:
    provider_name = "test-provider"

    def submit_generation(self, request):  # pragma: no cover - should never be called
        raise AssertionError("provider adapter should not be called for unsupported multi-output intents")

    def poll_generation(self, task_id: str, *, model_id: str, action: str):  # pragma: no cover
        raise AssertionError("provider adapter should not be called for unsupported multi-output intents")


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
    assert recipe.metadata["outputs"] == "image,video"
    assert recipe.metadata["requested_outputs"] == "image,video"
    assert recipe.metadata["primary_output"] == "image"


def test_execution_service_plan_marks_multi_output_as_unsupported():
    service = ExecutionEngineService(provider_adapter=_FailIfCalledProvider())
    intent = IntentRequest(
        intent_id="intent-multi-plan",
        prompt="Create both an image and a video",
        desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
    )

    plan = service.plan(intent)

    assert len(plan.recipe.steps) == 1
    assert plan.recipe.steps[0].output_type == MediaType.IMAGE
    assert plan.recipe.metadata["requested_outputs"] == "image,video"
    assert plan.match.status == MatchStatus.NO_MATCH
    assert plan.match.selected is None
    assert plan.match.missing_requirements == ("multi_output_not_supported",)


def test_execution_service_dispatch_rejects_unsupported_multi_output():
    service = ExecutionEngineService(provider_adapter=_FailIfCalledProvider())
    intent = IntentRequest(
        intent_id="intent-multi-dispatch",
        prompt="Create both an image and a video",
        desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
    )

    result = service.dispatch(intent)

    assert result.accepted is False
    assert result.admission.status == AdmissionStatus.REJECTED
    assert result.admission.reason == "multi_output_not_supported"
    assert result.details["reason"] == "multi_output_not_supported"
    assert result.details["match_status"] == MatchStatus.NO_MATCH.value
    assert result.details["requested_outputs"] == "image,video"
    snapshot = service.simulator.snapshot()
    assert snapshot.running_count == 0
    assert snapshot.queued_count == 0


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
