"""Execution service interface and default MVP implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..core.config import get_settings
from ..domain.enums import ExecutionRunStatus
from ..integrations.agentskillos_execution_service import AgentSkillOSExecutionService
from ..integrations.model_router import ModelRouteRequest, ModelRouteStatus, ModelRouter
from ..integrations.providers import (
    DashScopeProviderAdapter,
    GenerationRequest,
    GenerationResponse,
    MediaProviderAdapter,
)
from ..runtime.hardware_simulator import (
    AdmissionResult,
    AdmissionStatus,
    HardwareProfile,
    HardwareSimulator,
    WorkloadSpec,
)
from ..runtime.preview_policy import PreviewDecision, PreviewPolicy
from .agentskillos_wrapper import AgentSkillOSWrapper
from .contracts import (
    ExecutionRecipe,
    ExecutionRunDispatchStatus,
    IntentRequest,
    MatchStatus,
    MediaType,
    SolutionMatchResult,
)

_MULTI_OUTPUT_NOT_SUPPORTED = "multi_output_not_supported"


@dataclass(frozen=True)
class ExecutionPlan:
    """Planning output returned before dispatch."""

    recipe: ExecutionRecipe
    match: SolutionMatchResult
    preview: tuple[PreviewDecision, ...]
    candidate_artifacts: tuple[str, ...] = ()
    preview_candidates: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionDispatchResult:
    """Dispatch response consumed by backend-core."""

    accepted: bool
    admission: AdmissionResult
    provider_response: GenerationResponse | None = None
    run_id: str | None = None
    run_status: ExecutionRunDispatchStatus | None = None
    details: dict[str, str] = field(default_factory=dict)


class ExecutionService(Protocol):
    """Service interface callable from backend-core."""

    def plan(self, intent: IntentRequest) -> ExecutionPlan:
        """Normalize intent and resolve execution candidates."""

    def dispatch(self, intent: IntentRequest) -> ExecutionDispatchResult:
        """Plan and submit execution to runtime/provider layers."""


class ExecutionEngineService:
    """Default execution service implementation for MVP."""

    def __init__(
        self,
        *,
        hardware_simulator: HardwareSimulator | None = None,
        preview_policy: PreviewPolicy | None = None,
        provider_adapter: MediaProviderAdapter | None = None,
        wrapper: AgentSkillOSWrapper | None = None,
        model_router: ModelRouter | None = None,
        execution_service: AgentSkillOSExecutionService | None = None,
    ):
        self._simulator = hardware_simulator or HardwareSimulator(
            HardwareProfile(
                total_capacity_units=24,
                total_memory_mb=32_768,
                max_concurrency=3,
                max_queue_depth=8,
            )
        )
        self._preview_policy = preview_policy or PreviewPolicy()
        self._provider_adapter = provider_adapter or DashScopeProviderAdapter.from_settings(get_settings())
        self._wrapper = wrapper or AgentSkillOSWrapper()
        self._model_router = model_router or ModelRouter()
        self._execution_service = execution_service or AgentSkillOSExecutionService()

    def plan(self, intent: IntentRequest) -> ExecutionPlan:
        wrapper_plan = self._wrapper.plan(intent)
        preview = self._preview_policy.decide(wrapper_plan.recipe, self._simulator.snapshot())
        return ExecutionPlan(
            recipe=wrapper_plan.recipe,
            match=wrapper_plan.match,
            preview=preview,
            candidate_artifacts=wrapper_plan.candidate_artifacts,
            preview_candidates=wrapper_plan.preview_candidates,
            metadata=wrapper_plan.execution_metadata,
        )

    def dispatch(self, intent: IntentRequest) -> ExecutionDispatchResult:
        plan = self.plan(intent)

        if plan.match.status == MatchStatus.NO_MATCH or plan.match.selected is None:
            rejection_reason = self._resolve_no_match_reason(plan.match)
            rejected = AdmissionResult(
                status=AdmissionStatus.REJECTED,
                snapshot=self._simulator.snapshot(),
                reason=rejection_reason,
            )
            return ExecutionDispatchResult(
                accepted=False,
                admission=rejected,
                details={
                    "reason": rejection_reason,
                    "match_status": plan.match.status.value,
                    "requested_outputs": plan.recipe.metadata.get("requested_outputs", ""),
                },
            )

        workload = WorkloadSpec(
            workload_id=plan.recipe.recipe_id,
            capacity_units=plan.recipe.total_capacity_units,
            memory_mb=plan.recipe.total_memory_mb,
            duration_ticks=max((step.resources.expected_duration_ticks for step in plan.recipe.steps), default=1),
        )
        admission = self._simulator.submit(workload)
        if admission.status == AdmissionStatus.REJECTED:
            return ExecutionDispatchResult(
                accepted=False,
                admission=admission,
                details={"reason": admission.reason},
            )

        submitted_run = self._execution_service.submit_task(
            external_order_id=intent.intent_id,
            prompt=plan.recipe.prompt,
        )
        accepted = submitted_run.status in {
            ExecutionRunStatus.QUEUED,
            ExecutionRunStatus.PLANNING,
            ExecutionRunStatus.RUNNING,
            ExecutionRunStatus.SUCCEEDED,
        }
        return ExecutionDispatchResult(
            accepted=accepted,
            admission=admission,
            run_id=submitted_run.run_id,
            run_status=ExecutionRunDispatchStatus(submitted_run.status.value),
            details={
                "match_status": plan.match.status.value,
                "run_id": submitted_run.run_id,
                "run_status": submitted_run.status.value,
            },
        )

    def _submit_provider_request(self, prompt: str, model_id: str, output_type: MediaType) -> GenerationResponse | None:
        request = GenerationRequest(
            prompt=prompt,
            output_type=output_type,
            model_id=model_id,
            action="generation",
        )
        return self._provider_adapter.submit_generation(request)

    @staticmethod
    def _resolve_no_match_reason(match: SolutionMatchResult) -> str:
        if _MULTI_OUTPUT_NOT_SUPPORTED in match.missing_requirements:
            return _MULTI_OUTPUT_NOT_SUPPORTED
        if match.missing_requirements:
            return match.missing_requirements[0]
        return "no_provider_match"

    @property
    def simulator(self) -> HardwareSimulator:
        """Expose simulator for policy checks and tests."""
        return self._simulator
