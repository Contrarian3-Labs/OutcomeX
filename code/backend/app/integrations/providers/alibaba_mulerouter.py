"""Alibaba + MuleRouter adapter shell for image/video generation.

This module intentionally keeps network calls as placeholders for MVP while
retaining reusable endpoint metadata and response contracts.
"""

from __future__ import annotations

from uuid import uuid4

from ...execution.contracts import MediaType
from .base import GenerationRequest, GenerationResponse, ProviderTaskStatus
from .registry import ProviderEndpoint, provider_registry


_PROVIDER_NAME = "alibaba-mulerouter"

_DEFAULT_ENDPOINTS = (
    ProviderEndpoint(
        model_id="alibaba/wan2.6-t2i",
        action="generation",
        provider=_PROVIDER_NAME,
        output_type=MediaType.IMAGE,
        api_path="/vendors/alibaba/v1/wan2.6-t2i/generation",
        result_key="images",
        tags=("preview", "image"),
    ),
    ProviderEndpoint(
        model_id="alibaba/wan2.6-t2v",
        action="generation",
        provider=_PROVIDER_NAME,
        output_type=MediaType.VIDEO,
        api_path="/vendors/alibaba/v1/wan2.6-t2v/generation",
        result_key="videos",
        tags=("preview", "video"),
    ),
)

for _endpoint in _DEFAULT_ENDPOINTS:
    provider_registry.register(_endpoint)


class AlibabaMuleRouterAdapter:
    """Placeholder adapter with reusable endpoint envelope."""

    provider_name = _PROVIDER_NAME

    def submit_generation(self, request: GenerationRequest) -> GenerationResponse:
        endpoint = provider_registry.get(request.model_id, request.action)
        if endpoint is None:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                error=f"unknown_endpoint:{request.model_id}/{request.action}",
            )

        if endpoint.output_type != request.output_type:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                error=f"output_mismatch:{request.output_type.value}",
            )

        task_id = f"mule-{uuid4().hex[:12]}"
        return GenerationResponse(
            success=True,
            provider=self.provider_name,
            status=ProviderTaskStatus.QUEUED,
            task_id=task_id,
            metadata={
                "api_path": endpoint.api_path,
                "result_key": endpoint.result_key,
                "placeholder": True,
            },
        )

    def poll_generation(self, task_id: str, *, model_id: str, action: str) -> GenerationResponse:
        endpoint = provider_registry.get(model_id, action)
        if endpoint is None:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                task_id=task_id,
                error=f"unknown_endpoint:{model_id}/{action}",
            )

        # Placeholder completion signal; real provider polling will replace this.
        preview_url = f"https://placeholder.invalid/{task_id}.{endpoint.result_key}"
        return GenerationResponse(
            success=True,
            provider=self.provider_name,
            status=ProviderTaskStatus.SUCCEEDED,
            task_id=task_id,
            result_urls=(preview_url,),
            metadata={"placeholder": True},
        )

