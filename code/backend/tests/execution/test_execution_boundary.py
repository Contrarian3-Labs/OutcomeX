from typing import get_type_hints

from app.domain.enums import ExecutionRunStatus
from app.execution.contracts import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService, ExecutionPlan
from app.runtime.hardware_simulator import AdmissionStatus


class _ExecutionServiceSpy:
    def __init__(self):
        self.calls = []

    def submit_task(self, *, external_order_id: str, prompt: str, input_files=(), execution_strategy=ExecutionStrategy.QUALITY):
        self.calls.append(
            {
                "external_order_id": external_order_id,
                "prompt": prompt,
                "input_files": tuple(input_files),
                "execution_strategy": execution_strategy,
            }
        )

        class _Snapshot:
            run_id = "aso-run-boundary"
            status = ExecutionRunStatus.QUEUED

        return _Snapshot()


def test_execution_plan_boundary_uses_thin_submission_types() -> None:
    hints = get_type_hints(ExecutionPlan)
    assert hints["execution_request"] == dict[str, object]
    assert hints["metadata"] == dict[str, str]


def test_execution_service_plan_keeps_boundary_to_intent_files_and_strategy() -> None:
    service = ExecutionEngineService(execution_service=_ExecutionServiceSpy())

    plan = service.plan(
        IntentRequest(
            intent_id="intent-thin-plan",
            prompt="Create a product teaser",
            input_files=("brief.md", "reference.png"),
            execution_strategy=ExecutionStrategy.QUALITY,
        )
    )

    assert plan.execution_request == {
        "intent": "Create a product teaser",
        "files": ["brief.md", "reference.png"],
        "execution_strategy": "quality",
    }
    assert plan.metadata["gateway"] == "outcomex_agentskillos_thin.v1"
    assert plan.metadata["agentskillos_mode"] == "dag"


def test_execution_service_dispatch_uses_generic_workload_and_thin_submission() -> None:
    execution_service = _ExecutionServiceSpy()
    service = ExecutionEngineService(execution_service=execution_service)

    result = service.dispatch(
        IntentRequest(
            intent_id="intent-thin-dispatch",
            prompt="Generate a short teaser",
            input_files=("reference.png",),
            execution_strategy=ExecutionStrategy.EFFICIENCY,
        )
    )

    assert result.accepted is True
    assert result.admission.status in {AdmissionStatus.RUNNING, AdmissionStatus.QUEUED}
    assert result.details["gateway"] == "outcomex_agentskillos_thin.v1"
    assert result.details["execution_strategy"] == "efficiency"
    assert execution_service.calls == [
        {
            "external_order_id": "intent-thin-dispatch",
            "prompt": "Generate a short teaser",
            "input_files": ("reference.png",),
            "execution_strategy": ExecutionStrategy.EFFICIENCY,
        }
    ]


def test_execution_admission_state_is_shared_across_service_instances() -> None:
    first_wrapper = ExecutionEngineService(execution_service=_ExecutionServiceSpy())
    second_wrapper = ExecutionEngineService(execution_service=_ExecutionServiceSpy())

    statuses = []
    for idx in range(3):
        result = first_wrapper.dispatch(
            IntentRequest(
                intent_id=f"intent-shared-{idx}",
                prompt="Fill runtime slots",
                input_files=(),
                execution_strategy=ExecutionStrategy.SIMPLICITY,
            )
        )
        statuses.append(result.admission.status)

    overflow = second_wrapper.dispatch(
        IntentRequest(
            intent_id="intent-shared-overflow",
            prompt="Overflow should queue",
            input_files=(),
            execution_strategy=ExecutionStrategy.SIMPLICITY,
        )
    )

    assert statuses == [
        AdmissionStatus.RUNNING,
        AdmissionStatus.RUNNING,
        AdmissionStatus.RUNNING,
    ]
    assert overflow.admission.status == AdmissionStatus.QUEUED
