"""Recipe -> provider solution matching.

Pattern adapted from mulerouter-skills registry matching: score candidates from
registered endpoints and return a primary selection plus alternatives.
"""

from __future__ import annotations

from .contracts import (
    CandidateMatch,
    ExecutionConstraints,
    ExecutionRecipe,
    MatchStatus,
    SolutionMatchResult,
)
from ..integrations.providers.registry import ProviderEndpoint, provider_registry


def _score_candidate(step_model: str, endpoint: ProviderEndpoint, preferred_provider: str | None) -> CandidateMatch:
    score = 0.5
    reasons: list[str] = []

    if endpoint.model_id == step_model:
        score += 0.35
        reasons.append("model_exact_match")
    else:
        reasons.append("model_compatible")

    if preferred_provider and endpoint.provider == preferred_provider:
        score += 0.15
        reasons.append("preferred_provider")

    return CandidateMatch(
        provider=endpoint.provider,
        model_id=endpoint.model_id,
        action=endpoint.action,
        score=round(min(score, 1.0), 3),
        reasons=tuple(reasons),
    )


def match_recipe_to_solution(recipe: ExecutionRecipe, constraints: ExecutionConstraints) -> SolutionMatchResult:
    """Match a single-step recipe to the best provider/model candidate."""
    if not recipe.steps:
        return SolutionMatchResult(
            status=MatchStatus.NO_MATCH,
            selected=None,
            missing_requirements=("empty_recipe",),
        )

    if len(recipe.steps) > 1:
        return SolutionMatchResult(
            status=MatchStatus.NO_MATCH,
            selected=None,
            missing_requirements=("multi_step_recipe_not_supported",),
        )

    primary_step = recipe.steps[0]
    endpoints = [
        endpoint
        for endpoint in provider_registry.list_all()
        if endpoint.action == primary_step.action and endpoint.output_type == primary_step.output_type
    ]

    if not endpoints:
        return SolutionMatchResult(
            status=MatchStatus.NO_MATCH,
            selected=None,
            missing_requirements=(f"no_endpoint_for_{primary_step.output_type.value}",),
        )

    candidates = sorted(
        (
            _score_candidate(primary_step.model, endpoint, constraints.prefer_provider)
            for endpoint in endpoints
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )

    selected = candidates[0]
    if selected.model_id == primary_step.model:
        status = MatchStatus.MATCHED
    else:
        status = MatchStatus.FALLBACK

    return SolutionMatchResult(
        status=status,
        selected=selected,
        alternatives=tuple(candidates[1:]),
    )
