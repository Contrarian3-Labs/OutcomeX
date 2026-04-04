"""Provider endpoint registry.

Modeled after mulerouter-skills/core/registry.py but scoped to execution MVP.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...execution.contracts import MediaType


@dataclass(frozen=True)
class ProviderEndpoint:
    """Descriptor for one provider model action."""

    model_id: str
    action: str
    provider: str
    output_type: MediaType
    api_path: str
    result_key: str
    model_family: str
    tags: tuple[str, ...] = ()


class ProviderRegistry:
    """In-memory endpoint registry."""

    def __init__(self):
        self._endpoints: dict[str, ProviderEndpoint] = {}

    def register(self, endpoint: ProviderEndpoint) -> None:
        key = self._key(endpoint.model_id, endpoint.action)
        self._endpoints[key] = endpoint

    def get(self, model_id: str, action: str) -> ProviderEndpoint | None:
        return self._endpoints.get(self._key(model_id, action))

    def list_all(self) -> tuple[ProviderEndpoint, ...]:
        return tuple(self._endpoints.values())

    def list_for(self, *, action: str, output_type: MediaType) -> tuple[ProviderEndpoint, ...]:
        return tuple(
            endpoint
            for endpoint in self._endpoints.values()
            if endpoint.action == action and endpoint.output_type == output_type
        )

    @staticmethod
    def _key(model_id: str, action: str) -> str:
        return f"{model_id}/{action}"


provider_registry = ProviderRegistry()

provider_registry.register(
    ProviderEndpoint(
        model_id="qwen3.6-plus",
        action="generation",
        provider="dashscope",
        output_type=MediaType.TEXT,
        api_path="/chat/completions",
        result_key="text",
        model_family="qwen3.6",
        tags=("text", "reasoning", "multimodal"),
    )
)
provider_registry.register(
    ProviderEndpoint(
        model_id="wan2.6-t2i",
        action="generation",
        provider="dashscope",
        output_type=MediaType.IMAGE,
        api_path="/api/v1/services/aigc/image-generation/generation",
        result_key="images",
        model_family="wan2.6",
        tags=("image", "generation"),
    )
)
provider_registry.register(
    ProviderEndpoint(
        model_id="wan2.2-t2v-plus",
        action="generation",
        provider="dashscope",
        output_type=MediaType.VIDEO,
        api_path="/api/v1/services/aigc/video-generation/video-synthesis",
        result_key="videos",
        model_family="wan2.2",
        tags=("video", "generation"),
    )
)

