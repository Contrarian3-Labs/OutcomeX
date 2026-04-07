"""Execution service interface and default MVP implementation."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Protocol

from ..core.config import Settings, get_settings
from ..domain.enums import ExecutionRunStatus
from ..integrations.agentskillos_execution_service import AgentSkillOSExecutionService
from ..runtime.hardware_simulator import (
    AdmissionResult,
    AdmissionStatus,
    HardwareSimulator,
    WorkloadSpec,
    get_shared_hardware_simulator,
)
from .contracts import (
    ExecutionRunDispatchStatus,
    ExecutionStrategy,
    IntentRequest,
)


@dataclass(frozen=True)
class ExecutionPlan:
    """Thin submission contract from OutcomeX into AgentSkillOS."""

    execution_request: dict[str, object]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionDispatchResult:
    """Dispatch response consumed by backend-core."""

    accepted: bool
    admission: AdmissionResult
    run_id: str | None = None
    run_status: ExecutionRunDispatchStatus | None = None
    details: dict[str, str] = field(default_factory=dict)
    selected_plan: dict | None = None


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
        settings: Settings | None = None,
        hardware_simulator: HardwareSimulator | None = None,
        execution_service: AgentSkillOSExecutionService | None = None,
    ):
        self._settings = settings or get_settings()
        self._simulator = hardware_simulator or get_shared_hardware_simulator()
        self._execution_service = execution_service or AgentSkillOSExecutionService()

    def plan(self, intent: IntentRequest) -> ExecutionPlan:
        execution_request = {
            "intent": intent.prompt,
            "files": list(intent.input_files),
            "execution_strategy": intent.execution_strategy.value,
        }
        return ExecutionPlan(
            execution_request=execution_request,
            metadata={
                "gateway": "outcomex_agentskillos_thin.v1",
                "submission_status": "draft",
                "execution_strategy": intent.execution_strategy.value,
                "agentskillos_mode": self._settings.agentskillos_execution_mode,
                "input_file_count": str(len(intent.input_files)),
            },
        )

    def dispatch(self, intent: IntentRequest) -> ExecutionDispatchResult:
        plan = self.plan(intent)
        workload = self._estimate_workload(intent)
        admission = self._resolve_simulator(intent).submit(workload)
        if admission.status == AdmissionStatus.REJECTED:
            return ExecutionDispatchResult(
                accepted=False,
                admission=admission,
                details={"reason": admission.reason},
            )

        selected_plan_index = None
        if intent.context.get("selected_native_plan_index"):
            selected_plan_index = int(intent.context["selected_native_plan_index"])

        submit_kwargs = {
            "external_order_id": intent.intent_id,
            "prompt": intent.prompt,
            "input_files": intent.input_files,
            "execution_strategy": intent.execution_strategy,
        }
        if selected_plan_index is not None and self._supports_selected_plan_index():
            submit_kwargs["selected_plan_index"] = selected_plan_index

        submitted_run = self._execution_service.submit_task(**submit_kwargs)
        accepted = submitted_run.status in {
            ExecutionRunStatus.QUEUED,
            ExecutionRunStatus.PLANNING,
            ExecutionRunStatus.RUNNING,
            ExecutionRunStatus.SUCCEEDED,
        }
        details = {
            "gateway": str(plan.metadata["gateway"]),
            "run_id": submitted_run.run_id,
            "run_status": submitted_run.status.value,
            "execution_strategy": intent.execution_strategy.value,
        }
        if selected_plan_index is not None:
            details["selected_plan_index"] = str(selected_plan_index)
        return ExecutionDispatchResult(
            accepted=accepted,
            admission=admission,
            run_id=submitted_run.run_id,
            run_status=ExecutionRunDispatchStatus(submitted_run.status.value),
            details=details,
            selected_plan=getattr(submitted_run, "selected_plan", None),
        )

    def _resolve_simulator(self, intent: IntentRequest) -> HardwareSimulator:
        machine_id = intent.context.get("machine_id")
        if machine_id:
            return get_shared_hardware_simulator(machine_id)
        return self._simulator

    def _supports_selected_plan_index(self) -> bool:
        try:
            signature = inspect.signature(self._execution_service.submit_task)
        except (TypeError, ValueError):
            return False
        return "selected_plan_index" in signature.parameters

    @staticmethod
    def _estimate_workload(intent: IntentRequest) -> WorkloadSpec:
        strategy_profile = {
            ExecutionStrategy.QUALITY: (3, 2_048, 3),
            ExecutionStrategy.EFFICIENCY: (2, 1_024, 2),
            ExecutionStrategy.SIMPLICITY: (1, 768, 1),
        }
        capacity_units, memory_mb, duration_ticks = strategy_profile[intent.execution_strategy]
        file_count = len(intent.input_files)
        return WorkloadSpec(
            workload_id=intent.intent_id,
            capacity_units=capacity_units + min(file_count, 2),
            memory_mb=memory_mb + (min(file_count, 3) * 256),
            duration_ticks=duration_ticks,
        )

    @property
    def simulator(self) -> HardwareSimulator:
        """Expose simulator for policy checks and tests."""
        return self._simulator
