from app.execution.agentskillos_wrapper import AgentSkillOSWrapper
from app.execution.contracts import IntentRequest, MatchStatus, MediaType
from app.integrations.model_router import ModelRouteRequest, ModelRouteStatus, ModelRouter


def test_wrapper_plan_maps_intent_to_recipe_and_metadata() -> None:
    wrapper = AgentSkillOSWrapper()
    intent = IntentRequest(intent_id="intent-text", prompt="Summarize this runbook")

    result = wrapper.plan(intent)

    assert result.recipe.recipe_id == "recipe-intent-text"
    assert result.match.status == MatchStatus.MATCHED
    assert result.match.selected is not None
    assert result.match.selected.provider == "builtin"
    assert result.execution_metadata["planner"] == "agentskillos_wrapper.v1"
    assert result.execution_metadata["primary_output"] == "text"
    assert result.preview_candidates == ("text_snippet",)


def test_wrapper_plan_marks_multi_output_as_unsupported() -> None:
    wrapper = AgentSkillOSWrapper()
    intent = IntentRequest(
        intent_id="intent-multi",
        prompt="Make an image and video",
        desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
    )

    result = wrapper.plan(intent)

    assert result.match.status == MatchStatus.NO_MATCH
    assert result.match.selected is None
    assert result.match.missing_requirements == ("multi_output_not_supported",)


def test_model_router_prefers_exact_model_with_allowed_family() -> None:
    router = ModelRouter()

    route = router.route(
        ModelRouteRequest(
            output_type=MediaType.IMAGE,
            preferred_model_id="alibaba/wan2.6-t2i",
            allowed_model_families=("wan2.6",),
        )
    )

    assert route.status == ModelRouteStatus.MATCHED
    assert route.model_id == "alibaba/wan2.6-t2i"
    assert route.provider == "alibaba-mulerouter"
    assert route.model_family == "wan2.6"


def test_model_router_rejects_disallowed_model_families() -> None:
    router = ModelRouter()

    route = router.route(
        ModelRouteRequest(
            output_type=MediaType.IMAGE,
            preferred_model_id="alibaba/wan2.6-t2i",
            allowed_model_families=("forbidden-family",),
        )
    )

    assert route.status == ModelRouteStatus.NO_ROUTE
    assert route.model_id is None
    assert route.provider is None
