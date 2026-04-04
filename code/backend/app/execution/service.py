"""Execution service interface and default MVP implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..integrations.providers import (
    AlibabaMuleRouterAdapter,
    GenerationRequest,
    GenerationResponse,
    MediaProviderAdapter,
    ProviderTaskStatus,
)
from ..runtime.hardware_simulator import (
    AdmissionResult,
    AdmissionStatus,
    HardwareProfile,
    HardwareSimulator,
    WorkloadSpec,
)
from ..runtime.preview_policy import PreviewDecision, PreviewPolicy
from .contracts import IntentRequest, MatchStatus, MediaType
from .matcher import match_recipe_to_solution
from .normalizer import normalize_intent_to_recipe


@dataclass(frozen=True)
class ExecutionPlan:
    """Planning output returned before dispatch."""

    recipe: object
    match: object
    preview: tuple[PreviewDecision, ...]


@dataclass(frozen=True)
class ExecutionDispatchResult:
    """Dispatch response consumed by backend-core."""

    accepted: bool
    admission: AdmissionResult
    provider_response: GenerationResponse | None = None
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
        self._provider_adapter = provider_adapter or AlibabaMuleRouterAdapter()

    def plan(self, intent: IntentRequest) -> ExecutionPlan:
        recipe = normalize_intent_to_recipe(intent)
        match = match_recipe_to_solution(recipe, intent.constraints)
        preview = self._preview_policy.decide(recipe, self._simulator.snapshot())
        return ExecutionPlan(recipe=recipe, match=match, preview=preview)

    def dispatch(self, intent: IntentRequest) -> ExecutionDispatchResult:
        plan = self.plan(intent)

        if plan.match.status == MatchStatus.NO_MATCH or plan.match.selected is None:
            rejected = AdmissionResult(
                status=AdmissionStatus.REJECTED,
                snapshot=self._simulator.snapshot(),
                reason="no_provider_match",
            )
            return ExecutionDispatchResult(
                accepted=False,
                admission=rejected,
                details={"reason": "no_provider_match"},
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

        provider_response = self._submit_provider_request(plan.recipe.prompt, plan.match.selected.model_id, plan.recipe.steps[0].output_type)
        accepted = provider_response is None or provider_response.success
        return ExecutionDispatchResult(
            accepted=accepted,
            admission=admission,
            provider_response=provider_response,
            details={"match_status": plan.match.status.value},
        )

    def _submit_provider_request(self, prompt: str, model_id: str, output_type: MediaType) -> GenerationResponse | None:
        if output_type == MediaType.TEXT:
            return GenerationResponse(
                success=True,
                provider="builtin",
                status=ProviderTaskStatus.SUCCEEDED,
                result_urls=(),
                metadata={"mode": "inline_text"},
            )

        request = GenerationRequest(
            prompt=prompt,
            output_type=output_type,
            model_id=model_id,
            action="generation",
        )
        return self._provider_adapter.submit_generation(request)

    @property
    def simulator(self) -> HardwareSimulator:
        """Expose simulator for policy checks and tests."""
        return self._simulator

