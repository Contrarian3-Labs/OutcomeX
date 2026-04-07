from app.api.deps import get_dependency_container, get_execution_engine_service
from app.core.container import reset_container_cache
from app.execution.contracts import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService
from app.domain.enums import ExecutionRunStatus
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
            run_id = "aso-run-123"
            status = ExecutionRunStatus.QUEUED

        return _Snapshot()


def test_execution_service_plan_returns_thin_submission_metadata() -> None:
    service = ExecutionEngineService(execution_service=_ExecutionServiceSpy())

    plan = service.plan(
        IntentRequest(
            intent_id="intent-plan",
            prompt="Generate a teaser",
            input_files=("reference.png",),
            execution_strategy=ExecutionStrategy.SIMPLICITY,
        )
    )

    assert plan.execution_request == {
        "intent": "Generate a teaser",
        "files": ["reference.png"],
        "execution_strategy": "simplicity",
    }
    assert plan.metadata["gateway"] == "outcomex_agentskillos_thin.v1"
    assert plan.metadata["submission_status"] == "draft"


def test_execution_service_dispatch_forwards_only_thin_boundary_fields() -> None:
    execution_service = _ExecutionServiceSpy()
    service = ExecutionEngineService(execution_service=execution_service)

    result = service.dispatch(
        IntentRequest(
            intent_id="intent-dispatch",
            prompt="Generate image from reference",
            input_files=("reference.png", "notes.txt"),
            execution_strategy=ExecutionStrategy.EFFICIENCY,
        )
    )

    assert result.accepted is True
    assert result.admission.status in {AdmissionStatus.RUNNING, AdmissionStatus.QUEUED}
    assert execution_service.calls == [
        {
            "external_order_id": "intent-dispatch",
            "prompt": "Generate image from reference",
            "input_files": ("reference.png", "notes.txt"),
            "execution_strategy": ExecutionStrategy.EFFICIENCY,
        }
    ]
    assert result.run_id == "aso-run-123"
    assert result.run_status.value == "queued"
    assert result.details["run_status"] == "queued"


def test_dependency_execution_service_shares_admission_state_and_reset_hook() -> None:
    container = get_dependency_container()
    service_a = get_execution_engine_service(
        hardware_simulator=container.hardware_simulator,
        execution_service=_ExecutionServiceSpy(),
    )
    service_b = get_execution_engine_service(
        hardware_simulator=container.hardware_simulator,
        execution_service=_ExecutionServiceSpy(),
    )

    for idx in range(3):
        dispatch = service_a.dispatch(
            IntentRequest(
                intent_id=f"intent-di-{idx}",
                prompt="Use shared simulator via deps",
                input_files=(),
                execution_strategy=ExecutionStrategy.SIMPLICITY,
            )
        )
        assert dispatch.admission.status == AdmissionStatus.RUNNING

    overflow = service_b.dispatch(
        IntentRequest(
            intent_id="intent-di-overflow",
            prompt="Should be queued on shared occupancy",
            input_files=(),
            execution_strategy=ExecutionStrategy.SIMPLICITY,
        )
    )
    assert overflow.admission.status == AdmissionStatus.QUEUED

    reset_container_cache()
    refreshed = get_execution_engine_service(
        hardware_simulator=get_dependency_container().hardware_simulator,
        execution_service=_ExecutionServiceSpy(),
    ).dispatch(
        IntentRequest(
            intent_id="intent-after-reset",
            prompt="reset hook clears shared occupancy",
            input_files=(),
            execution_strategy=ExecutionStrategy.SIMPLICITY,
        )
    )
    assert refreshed.admission.status == AdmissionStatus.RUNNING


def test_execution_admission_state_is_isolated_per_machine_when_machine_context_is_present() -> None:
    service = ExecutionEngineService(execution_service=_ExecutionServiceSpy())

    for idx in range(3):
        result = service.dispatch(
            IntentRequest(
                intent_id=f"intent-machine-a-{idx}",
                prompt="Fill runtime slots on machine A",
                input_files=(),
                execution_strategy=ExecutionStrategy.SIMPLICITY,
                context={"machine_id": "machine-a"},
            )
        )
        assert result.admission.status == AdmissionStatus.RUNNING

    isolated = service.dispatch(
        IntentRequest(
            intent_id="intent-machine-b-0",
            prompt="Machine B should still have fresh capacity",
            input_files=(),
            execution_strategy=ExecutionStrategy.SIMPLICITY,
            context={"machine_id": "machine-b"},
        )
    )

    assert isolated.admission.status == AdmissionStatus.RUNNING
