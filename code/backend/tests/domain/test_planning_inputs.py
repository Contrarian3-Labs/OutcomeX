from app.domain.planning import build_fast_recommended_plans, build_recommended_plans
from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_bridge import (
    AgentSkillOSNativePlan,
    AgentSkillOSPlanningResult,
)


class BridgeStub:
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.discover_calls: list[str] = []

    def generate_plans(self, task: str, *, files: tuple[str, ...] = ()):
        self.generate_calls.append({"task": task, "files": files})
        return AgentSkillOSPlanningResult(
            plans=(
                AgentSkillOSNativePlan(
                    plan_index=0,
                    name="quality-plan",
                    description="quality path",
                    nodes=(),
                ),
                AgentSkillOSNativePlan(
                    plan_index=1,
                    name="efficiency-plan",
                    description="efficiency path",
                    nodes=(),
                ),
                AgentSkillOSNativePlan(
                    plan_index=2,
                    name="simplicity-plan",
                    description="simplicity path",
                    nodes=(),
                ),
            ),
            skill_ids=("skill.image", "skill.brand"),
            source="stub",
        )

    def discover_skills(self, task: str):
        self.discover_calls.append(task)
        raise AssertionError("discover_skills should not be called when native plans exist")


def test_build_recommended_plans_passes_input_files_to_agentskillos_bridge() -> None:
    bridge = BridgeStub()

    plans = build_recommended_plans(
        user_id="user-1",
        chat_session_id="chat-1",
        user_message="Create a launch-ready teaser campaign",
        input_files=("brief.pdf", "brand-guide.png"),
        bridge=bridge,
    )

    assert bridge.generate_calls == [
        {
            "task": "Create a launch-ready teaser campaign",
            "files": ("brief.pdf", "brand-guide.png"),
        }
    ]
    assert [plan.strategy for plan in plans] == [
        ExecutionStrategy.QUALITY,
        ExecutionStrategy.EFFICIENCY,
        ExecutionStrategy.SIMPLICITY,
    ]


def test_build_recommended_plans_reorders_native_plans_to_match_requested_mode() -> None:
    bridge = BridgeStub()

    plans = build_recommended_plans(
        user_id="user-1",
        chat_session_id="chat-1",
        user_message="Create a launch-ready teaser campaign",
        preferred_strategy=ExecutionStrategy.SIMPLICITY,
        input_files=("brief.pdf",),
        bridge=bridge,
    )

    assert [plan.strategy for plan in plans] == [
        ExecutionStrategy.SIMPLICITY,
        ExecutionStrategy.QUALITY,
        ExecutionStrategy.EFFICIENCY,
    ]
    assert plans[0].native_plan_index == 2


class FailedPlanningBridgeStub:
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.discover_calls: list[str] = []

    def generate_plans(self, task: str, *, files: tuple[str, ...] = ()):
        self.generate_calls.append({"task": task, "files": files})
        return AgentSkillOSPlanningResult(
            plans=(),
            skill_ids=(),
            source="agentskillos_failed",
            error="agentskillos_discovery_timeout",
        )

    def discover_skills(self, task: str):
        self.discover_calls.append(task)
        raise AssertionError("discover_skills should not be called after planning already failed")


def test_build_recommended_plans_falls_back_immediately_after_planning_failure() -> None:
    bridge = FailedPlanningBridgeStub()

    plans = build_recommended_plans(
        user_id="user-1",
        chat_session_id="chat-1",
        user_message="Create a launch-ready teaser campaign",
        planning_context_key="ctx",
        bridge=bridge,
    )

    assert bridge.generate_calls == [
        {
            "task": "Create a launch-ready teaser campaign",
            "files": (),
        }
    ]
    assert bridge.discover_calls == []
    assert [plan.strategy for plan in plans] == [
        ExecutionStrategy.QUALITY,
        ExecutionStrategy.EFFICIENCY,
        ExecutionStrategy.SIMPLICITY,
    ]


def test_build_fast_recommended_plans_is_deterministic_without_bridge() -> None:
    plans = build_fast_recommended_plans(
        user_id="user-1",
        chat_session_id="chat-1",
        user_message="Create a launch-ready teaser campaign",
        preferred_strategy=ExecutionStrategy.EFFICIENCY,
        planning_context_key="ctx_fast",
    )

    assert [plan.strategy for plan in plans] == [
        ExecutionStrategy.EFFICIENCY,
        ExecutionStrategy.QUALITY,
        ExecutionStrategy.SIMPLICITY,
    ]
    assert [plan.native_plan_index for plan in plans] == [1, 0, 2]
    assert all(plan.plan_id for plan in plans)
