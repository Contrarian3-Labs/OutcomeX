from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_bridge import AgentSkillOSBridge, AgentSkillOSPlanningResult


@dataclass(frozen=True)
class RecommendedPlan:
    plan_id: str
    strategy: ExecutionStrategy
    title: str
    summary: str
    why_this_plan: str
    tradeoff: str
    native_plan_index: int | None = None
    native_plan_name: str = ""
    native_plan_description: str = ""
    native_plan_nodes: tuple[dict[str, object], ...] = ()
    native_skill_ids: tuple[str, ...] = ()


def _normalize_message(user_message: str) -> str:
    return " ".join((user_message or "").split())


def _stable_plan_id(*, user_id: str, chat_session_id: str, normalized_message: str, strategy: ExecutionStrategy) -> str:
    digest = sha256(
        f"v1|{user_id}|{chat_session_id}|{normalized_message.lower()}|{strategy.value}".encode("utf-8")
    ).hexdigest()[:12]
    return f"plan_{strategy.value}_{digest}"


def _goal_snippet(normalized_message: str) -> str:
    if not normalized_message:
        return "your delivery goal"
    if len(normalized_message) <= 88:
        return normalized_message
    return f"{normalized_message[:85].rstrip()}..."


def _strategy_for_index(index: int) -> ExecutionStrategy:
    if index == 1:
        return ExecutionStrategy.EFFICIENCY
    if index == 2:
        return ExecutionStrategy.SIMPLICITY
    return ExecutionStrategy.QUALITY


def _default_native_index(strategy: ExecutionStrategy) -> int:
    return {
        ExecutionStrategy.QUALITY: 0,
        ExecutionStrategy.EFFICIENCY: 1,
        ExecutionStrategy.SIMPLICITY: 2,
    }[strategy]


def _why_this_plan(strategy: ExecutionStrategy, *, has_native_plan: bool, has_skill_signal: bool) -> str:
    if strategy == ExecutionStrategy.QUALITY:
        if has_native_plan:
            return "Best when you want AgentSkillOS to use the deepest native DAG with more preparation and validation stages."
        if has_skill_signal:
            return "Best when the result quality bar is high and the output may benefit from broader tool and skill coverage."
        return "Best when the result quality bar is high and you want extra validation before confirmation."
    if strategy == ExecutionStrategy.EFFICIENCY:
        if has_native_plan:
            return "Best when you want the native DAG to maximize parallel work without sacrificing delivery reliability."
        return "Best when you want a reliable result quickly without paying for the heaviest path."
    if has_native_plan:
        return "Best when you want the native DAG to keep only the essential steps and reach a usable result with minimal overhead."
    return "Best when you want a clean first pass, a prototype, or the simplest route to a usable result."


def _tradeoff(strategy: ExecutionStrategy) -> str:
    if strategy == ExecutionStrategy.QUALITY:
        return "Higher runtime and longer turnaround in exchange for the strongest finish."
    if strategy == ExecutionStrategy.EFFICIENCY:
        return "Less depth than the quality-first path, but faster and more parallel."
    return "Lowest overhead, but with fewer refinement steps and less redundancy."


def _fallback_plans(
    *,
    user_id: str,
    chat_session_id: str,
    normalized_message: str,
    has_skill_signal: bool,
) -> tuple[RecommendedPlan, ...]:
    goal = _goal_snippet(normalized_message)
    titles = {
        ExecutionStrategy.QUALITY: "Best Quality Delivery",
        ExecutionStrategy.EFFICIENCY: "Fastest Balanced Delivery",
        ExecutionStrategy.SIMPLICITY: "Leanest Path to Result",
    }
    summaries = {
        ExecutionStrategy.QUALITY: f"Use the highest-confidence delivery path for {goal}, with extra validation before final handoff.",
        ExecutionStrategy.EFFICIENCY: f"Use a balanced execution path for {goal}, optimizing for speed while keeping delivery quality solid.",
        ExecutionStrategy.SIMPLICITY: f"Use the minimum viable execution path for {goal}, so the first deliverable arrives with the least orchestration overhead.",
    }
    plans: list[RecommendedPlan] = []
    for strategy in (
        ExecutionStrategy.QUALITY,
        ExecutionStrategy.EFFICIENCY,
        ExecutionStrategy.SIMPLICITY,
    ):
        plans.append(
            RecommendedPlan(
                plan_id=_stable_plan_id(
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    normalized_message=normalized_message,
                    strategy=strategy,
                ),
                strategy=strategy,
                title=titles[strategy],
                summary=summaries[strategy],
                why_this_plan=_why_this_plan(strategy, has_native_plan=False, has_skill_signal=has_skill_signal),
                tradeoff=_tradeoff(strategy),
                native_plan_index=_default_native_index(strategy),
                native_plan_name=titles[strategy],
                native_plan_description=summaries[strategy],
            )
        )
    return tuple(plans)


def _native_plans_to_recommendations(
    *,
    user_id: str,
    chat_session_id: str,
    normalized_message: str,
    planning_result: AgentSkillOSPlanningResult,
) -> tuple[RecommendedPlan, ...]:
    plans: list[RecommendedPlan] = []
    for native_plan in planning_result.plans:
        strategy = _strategy_for_index(native_plan.plan_index)
        summary = native_plan.description or f"AgentSkillOS native {strategy.value} path for {_goal_snippet(normalized_message)}."
        plans.append(
            RecommendedPlan(
                plan_id=_stable_plan_id(
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    normalized_message=normalized_message,
                    strategy=strategy,
                ),
                strategy=strategy,
                title=native_plan.name or f"{strategy.value.title()} Plan",
                summary=summary,
                why_this_plan=_why_this_plan(strategy, has_native_plan=True, has_skill_signal=bool(planning_result.skill_ids)),
                tradeoff=_tradeoff(strategy),
                native_plan_index=native_plan.plan_index,
                native_plan_name=native_plan.name,
                native_plan_description=summary,
                native_plan_nodes=native_plan.nodes,
                native_skill_ids=planning_result.skill_ids,
            )
        )
    return tuple(plans)


def build_recommended_plans(
    *,
    user_id: str,
    chat_session_id: str,
    user_message: str,
    bridge: AgentSkillOSBridge | None = None,
) -> tuple[RecommendedPlan, ...]:
    normalized = _normalize_message(user_message)
    bridge = bridge or AgentSkillOSBridge()
    planning_result = bridge.generate_plans(normalized or user_message)
    if planning_result.plans:
        return _native_plans_to_recommendations(
            user_id=user_id,
            chat_session_id=chat_session_id,
            normalized_message=normalized,
            planning_result=planning_result,
        )

    discovery = bridge.discover_skills(normalized or user_message)
    return _fallback_plans(
        user_id=user_id,
        chat_session_id=chat_session_id,
        normalized_message=normalized,
        has_skill_signal=bool(discovery.skill_ids),
    )


def summarize_plan_from_chat(user_message: str) -> str:
    normalized = _normalize_message(user_message)
    if not normalized:
        return "Clarify your goal and constraints to receive a recommended plan."

    return build_recommended_plans(
        user_id="summary",
        chat_session_id="summary",
        user_message=normalized,
        bridge=None,
    )[0].summary


def select_recommended_plan(
    plans: tuple[RecommendedPlan, ...],
    *,
    selected_plan_id: str | None,
    execution_strategy: ExecutionStrategy,
) -> RecommendedPlan | None:
    if selected_plan_id:
        for plan in plans:
            if plan.plan_id == selected_plan_id:
                return plan
        return None

    for plan in plans:
        if plan.strategy == execution_strategy:
            return plan
    return plans[0] if plans else None
