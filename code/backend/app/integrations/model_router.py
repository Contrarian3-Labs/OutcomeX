"""Model routing boundary above provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..execution.contracts import MediaType
from .providers.registry import ProviderEndpoint, provider_registry


class ModelRouteStatus(str, Enum):
    MATCHED = "matched"
    FALLBACK = "fallback"
    NO_ROUTE = "no_route"


@dataclass(frozen=True)
class ModelRouteRequest:
    output_type: MediaType
    action: str = "generation"
    preferred_model_id: str | None = None
    preferred_provider: str | None = None
    allowed_model_families: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelRoute:
    status: ModelRouteStatus
    provider: str | None
    model_id: str | None
    action: str
    output_type: MediaType
    model_family: str | None = None
    reasons: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


def _model_family(model_id: str) -> str:
    if "/" in model_id:
        model_leaf = model_id.split("/", 1)[1]
    else:
        model_leaf = model_id
    return model_leaf.split("-", 1)[0]


class ModelRouter:
    """Select provider/model endpoint from capability + policy constraints."""

    def route(self, request: ModelRouteRequest) -> ModelRoute:
        candidates = [
            endpoint
            for endpoint in provider_registry.list_all()
            if endpoint.action == request.action and endpoint.output_type == request.output_type
        ]

        allowed_families = set(request.allowed_model_families)
        if allowed_families:
            candidates = [endpoint for endpoint in candidates if endpoint.model_family in allowed_families]

        if not candidates:
            return ModelRoute(
                status=ModelRouteStatus.NO_ROUTE,
                provider=None,
                model_id=None,
                action=request.action,
                output_type=request.output_type,
                reasons=("no_endpoint_for_policy",),
            )

        preferred_family = _model_family(request.preferred_model_id) if request.preferred_model_id else None

        def score(endpoint: ProviderEndpoint) -> float:
            value = 0.5
            if request.preferred_model_id and endpoint.model_id == request.preferred_model_id:
                value += 0.35
            if request.preferred_provider and endpoint.provider == request.preferred_provider:
                value += 0.1
            if preferred_family and endpoint.model_family == preferred_family:
                value += 0.05
            return value

        selected = sorted(candidates, key=score, reverse=True)[0]
        status = (
            ModelRouteStatus.MATCHED
            if request.preferred_model_id and selected.model_id == request.preferred_model_id
            else ModelRouteStatus.FALLBACK
        )

        return ModelRoute(
            status=status,
            provider=selected.provider,
            model_id=selected.model_id,
            action=selected.action,
            output_type=selected.output_type,
            model_family=selected.model_family,
            reasons=(f"score:{round(score(selected), 3)}",),
            metadata={"api_path": selected.api_path},
        )
