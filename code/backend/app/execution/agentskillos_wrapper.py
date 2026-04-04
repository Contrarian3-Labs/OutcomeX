"""AgentSkillOS-style wrapper for OutcomeX execution planning.

This wrapper keeps planning deterministic for Phase 1 while reserving a stable
boundary where a richer orchestration engine can be integrated later.
"""

from __future__ import annotations

from .contracts import (
    IntentRequest,
    MatchStatus,
    MediaType,
    SolutionMatchResult,
    WrapperPlanResult,
)
from .matcher import match_recipe_to_solution
from .normalizer import normalize_intent_to_recipe

_MULTI_OUTPUT_NOT_SUPPORTED = "multi_output_not_supported"


def _model_family(model_id: str) -> str:
    if "/" in model_id:
        model_leaf = model_id.split("/", 1)[1]
    else:
        model_leaf = model_id
    return model_leaf.split("-", 1)[0]


class AgentSkillOSWrapper:
    """Internal orchestration facade compatible with current execution types."""

    planner_version = "agentskillos_wrapper.v1"

    def plan(self, intent: IntentRequest) -> WrapperPlanResult:
        recipe = normalize_intent_to_recipe(intent)

        if len(intent.desired_outputs) > 1:
            match = SolutionMatchResult(
                status=MatchStatus.NO_MATCH,
                selected=None,
                missing_requirements=(_MULTI_OUTPUT_NOT_SUPPORTED,),
            )
        else:
            match = match_recipe_to_solution(recipe, intent.constraints)

        selected_model = match.selected.model_id if match.selected is not None else recipe.steps[0].model
        metadata = {
            "planner": self.planner_version,
            "requested_outputs": recipe.metadata.get("requested_outputs", ""),
            "primary_output": recipe.metadata.get("primary_output", ""),
            "match_status": match.status.value,
            "selected_provider": match.selected.provider if match.selected is not None else "",
            "selected_model": match.selected.model_id if match.selected is not None else "",
            "model_family": _model_family(selected_model),
        }

        primary_output = recipe.metadata.get("primary_output", "")
        preview_candidate = "none"
        if primary_output == MediaType.TEXT.value:
            preview_candidate = "text_snippet"
        elif primary_output == MediaType.IMAGE.value:
            preview_candidate = "image_thumbnail"
        elif primary_output == MediaType.VIDEO.value:
            preview_candidate = "video_poster"

        return WrapperPlanResult(
            recipe=recipe,
            match=match,
            candidate_artifacts=(f"{primary_output}_artifact",) if primary_output else (),
            preview_candidates=(preview_candidate,),
            execution_metadata=metadata,
        )
