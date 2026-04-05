from app.execution.contracts import (
    CandidateMatch,
    ExecutionRecipe,
    ExecutionStep,
    IntentRequest,
    MatchStatus,
    MediaType,
    ResourceEstimate,
    SolutionMatchResult,
    WrapperPlanResult,
)
from app.execution.service import ExecutionEngineService
from app.domain.enums import ExecutionRunStatus
from app.integrations.model_router import ModelRoute, ModelRouteRequest, ModelRouteStatus
from app.integrations.providers.base import GenerationResponse, ProviderTaskStatus
from app.runtime.hardware_simulator import AdmissionStatus


def _build_recipe(*, output_type: MediaType, model: str) -> ExecutionRecipe:
    return ExecutionRecipe(
        recipe_id=f"recipe-{output_type.value}",
        source_intent_id=f"intent-{output_type.value}",
        prompt=f"Generate {output_type.value}",
        steps=(
            ExecutionStep(
                step_id="s1",
                provider="dashscope",
                model=model,
                action="generation",
                output_type=output_type,
                resources=ResourceEstimate(capacity_units=3, memory_mb=2_048, expected_duration_ticks=2),
            ),
        ),
        metadata={"requested_outputs": output_type.value, "primary_output": output_type.value},
    )


class _WrapperSpy:
    def __init__(self, result: WrapperPlanResult):
        self.result = result
        self.calls: list[IntentRequest] = []

    def plan(self, intent: IntentRequest) -> WrapperPlanResult:
        self.calls.append(intent)
        return self.result


class _RouterSpy:
    def __init__(self, route_result: ModelRoute):
        self.route_result = route_result
        self.calls: list[ModelRouteRequest] = []

    def route(self, request: ModelRouteRequest) -> ModelRoute:
        self.calls.append(request)
        return self.route_result


class _ProviderSpy:
    provider_name = "provider-spy"

    def __init__(self):
        self.submitted = []

    def submit_generation(self, request):
        self.submitted.append(request)
        return GenerationResponse(
            success=True,
            provider=self.provider_name,
            status=ProviderTaskStatus.QUEUED,
            task_id="task-123",
        )

    def poll_generation(self, task_id: str, *, model_id: str, action: str):  # pragma: no cover
        raise NotImplementedError


class _ExecutionServiceSpy:
    def __init__(self):
        self.calls = []

    def submit_task(self, *, external_order_id: str, prompt: str, input_files=()):
        self.calls.append(
            {
                "external_order_id": external_order_id,
                "prompt": prompt,
                "input_files": tuple(input_files),
            }
        )

        class _Snapshot:
            run_id = "aso-run-123"
            status = ExecutionRunStatus.QUEUED

        return _Snapshot()


def test_execution_service_plan_uses_wrapper_output() -> None:
    wrapper_result = WrapperPlanResult(
        recipe=_build_recipe(output_type=MediaType.IMAGE, model="wan2.6-t2i"),
        match=SolutionMatchResult(
            status=MatchStatus.MATCHED,
            selected=CandidateMatch(
                provider="dashscope",
                model_id="wan2.6-t2i",
                action="generation",
                score=1.0,
            ),
        ),
        execution_metadata={"planner": "wrapper-spy"},
    )
    wrapper = _WrapperSpy(wrapper_result)
    router = _RouterSpy(
        ModelRoute(
            status=ModelRouteStatus.MATCHED,
            provider="dashscope",
            model_id="wan2.6-t2i",
            action="generation",
            output_type=MediaType.IMAGE,
            model_family="wan2.6",
        )
    )
    service = ExecutionEngineService(wrapper=wrapper, model_router=router, provider_adapter=_ProviderSpy())

    plan = service.plan(IntentRequest(intent_id="intent-plan", prompt="Generate image", desired_outputs=(MediaType.IMAGE,)))

    assert len(wrapper.calls) == 1
    assert plan.recipe == wrapper_result.recipe
    assert plan.match == wrapper_result.match
    assert plan.metadata["planner"] == "wrapper-spy"


def test_execution_service_dispatch_uses_model_router_selection() -> None:
    wrapper_result = WrapperPlanResult(
        recipe=_build_recipe(output_type=MediaType.IMAGE, model="legacy-image"),
        match=SolutionMatchResult(
            status=MatchStatus.FALLBACK,
            selected=CandidateMatch(
                provider="dashscope",
                model_id="legacy-image",
                action="generation",
                score=0.5,
            ),
        ),
    )
    wrapper = _WrapperSpy(wrapper_result)
    route = ModelRoute(
        status=ModelRouteStatus.FALLBACK,
        provider="dashscope",
        model_id="wan2.6-t2i",
        action="generation",
        output_type=MediaType.IMAGE,
        model_family="wan2.6",
    )
    router = _RouterSpy(route)
    provider = _ProviderSpy()
    execution_service = _ExecutionServiceSpy()
    service = ExecutionEngineService(
        wrapper=wrapper,
        model_router=router,
        provider_adapter=provider,
        execution_service=execution_service,
    )

    result = service.dispatch(IntentRequest(intent_id="intent-dispatch", prompt="Generate image", desired_outputs=(MediaType.IMAGE,)))

    assert result.accepted is True
    assert result.admission.status in {AdmissionStatus.RUNNING, AdmissionStatus.QUEUED}
    assert execution_service.calls[0]["external_order_id"] == "intent-dispatch"
    assert execution_service.calls[0]["prompt"] == "Generate image"
    assert result.run_id == "aso-run-123"
    assert result.run_status.value == "queued"
    assert result.details["run_status"] == "queued"


def test_execution_service_dispatch_rejects_multi_output_without_provider_call() -> None:
    class _FailIfCalledProvider(_ProviderSpy):
        def submit_generation(self, request):  # pragma: no cover - should not be called
            raise AssertionError("provider adapter should not be called for unsupported multi-output intents")

    service = ExecutionEngineService(provider_adapter=_FailIfCalledProvider())
    result = service.dispatch(
        IntentRequest(
            intent_id="intent-multi",
            prompt="Generate image and video",
            desired_outputs=(MediaType.IMAGE, MediaType.VIDEO),
        )
    )

    assert result.accepted is False
    assert result.admission.status == AdmissionStatus.REJECTED
    assert result.details["reason"] == "multi_output_not_supported"
