from app.core.config import Settings
from app.integrations.agentskillos_bridge import AgentSkillOSBridge, AgentSkillOSDiscoveryResult
from app.execution.agentskillos_wrapper import AgentSkillOSWrapper
from app.execution.contracts import IntentRequest, MatchStatus, MediaType
from app.integrations.model_router import ModelRouteRequest, ModelRouteStatus, ModelRouter


class _BridgeSpy(AgentSkillOSBridge):
    def __init__(self, result: AgentSkillOSDiscoveryResult):
        self._result = result
        self.calls: list[str] = []

    def discover_skills(self, task: str) -> AgentSkillOSDiscoveryResult:
        self.calls.append(task)
        return self._result


def test_wrapper_plan_maps_intent_to_recipe_and_metadata() -> None:
    wrapper = AgentSkillOSWrapper(
        bridge=_BridgeSpy(
            AgentSkillOSDiscoveryResult(
                skill_ids=("summarize", "docx"),
                source="agentskillos_discovery",
                repo_root="/tmp/AgentSkillOS",
            )
        )
    )
    intent = IntentRequest(intent_id="intent-text", prompt="Summarize this runbook")

    result = wrapper.plan(intent)

    assert result.recipe.recipe_id == "recipe-intent-text"
    assert result.match.status == MatchStatus.MATCHED
    assert result.match.selected is not None
    assert result.match.selected.provider == "dashscope"
    assert result.execution_metadata["planner"] == "agentskillos_wrapper.v1"
    assert result.execution_metadata["primary_output"] == "text"
    assert result.execution_metadata["planning_source"] == "agentskillos_discovery"
    assert result.execution_metadata["agentskillos_skill_ids"] == "summarize,docx"
    assert result.preview_candidates == ("text_snippet",)


def test_model_router_routes_text_requests_to_dashscope() -> None:
    router = ModelRouter()

    route = router.route(
        ModelRouteRequest(
            output_type=MediaType.TEXT,
            preferred_model_id="qwen3.6-plus",
        )
    )

    assert route.status == ModelRouteStatus.MATCHED
    assert route.model_id == "qwen3.6-plus"
    assert route.provider == "dashscope"
    assert route.model_family == "qwen3.6"


def test_wrapper_plan_marks_multi_output_as_unsupported() -> None:
    wrapper = AgentSkillOSWrapper(
        bridge=_BridgeSpy(
            AgentSkillOSDiscoveryResult(
                skill_ids=(),
                source="agentskillos_failed",
                error="repo_root_not_found",
            )
        )
    )
    intent = IntentRequest(
        intent_id="intent-multi",
        prompt="Make an image and video",
        desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
    )

    result = wrapper.plan(intent)

    assert result.match.status == MatchStatus.NO_MATCH
    assert result.match.selected is None
    assert result.match.missing_requirements == ("multi_output_not_supported",)
    assert result.execution_metadata["planning_source"] == "agentskillos_failed"
    assert result.execution_metadata["agentskillos_error"] == "repo_root_not_found"


def test_model_router_prefers_exact_model_with_allowed_family() -> None:
    router = ModelRouter()

    route = router.route(
        ModelRouteRequest(
            output_type=MediaType.IMAGE,
            preferred_model_id="wan2.6-t2i",
            allowed_model_families=("wan2.6",),
        )
    )

    assert route.status == ModelRouteStatus.MATCHED
    assert route.model_id == "wan2.6-t2i"
    assert route.provider == "dashscope"
    assert route.model_family == "wan2.6"


def test_model_router_rejects_disallowed_model_families() -> None:
    router = ModelRouter()

    route = router.route(
        ModelRouteRequest(
            output_type=MediaType.IMAGE,
            preferred_model_id="wan2.6-t2i",
            allowed_model_families=("forbidden-family",),
        )
    )

    assert route.status == ModelRouteStatus.NO_ROUTE
    assert route.model_id is None
    assert route.provider is None


def test_agentskillos_bridge_parses_discovery_output() -> None:
    class _Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, command, *, env, cwd, timeout_seconds):
            self.calls.append((command, env, cwd, timeout_seconds))
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": '{"skills":["generate-image","media-processing"]}',
                    "stderr": "",
                },
            )()

    runner = _Runner()
    bridge = AgentSkillOSBridge(settings=Settings(dashscope_api_key="test-key"), runner=runner)
    bridge._resolve_repo_root = lambda: __import__("pathlib").Path("/tmp/AgentSkillOS")  # type: ignore[method-assign]
    bridge._resolve_python_executable = lambda _repo_root: __import__("pathlib").Path("/tmp/AgentSkillOS/.venv/bin/python")  # type: ignore[method-assign]

    result = bridge.discover_skills("Make a teaser video")

    assert result.source == "agentskillos_discovery"
    assert result.skill_ids == ("generate-image", "media-processing")
    _, env, cwd, timeout_seconds = runner.calls[0]
    assert env["LLM_BASE_URL"].endswith("/compatible-mode/v1")
    assert cwd == "/tmp/AgentSkillOS"
    assert timeout_seconds == 120.0
