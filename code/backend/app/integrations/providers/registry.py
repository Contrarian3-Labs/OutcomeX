"""Provider endpoint registry.

Modeled after mulerouter-skills/core/registry.py but scoped to execution MVP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    @staticmethod
    def _key(model_id: str, action: str) -> str:
        return f"{model_id}/{action}"


provider_registry = ProviderRegistry()

