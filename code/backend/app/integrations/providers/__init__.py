"""Provider abstractions and concrete adapters."""

from .base import GenerationRequest, GenerationResponse, MediaProviderAdapter, ProviderTaskStatus
from .dashscope import DashScopeProviderAdapter
from .registry import ProviderEndpoint, provider_registry

__all__ = [
    "DashScopeProviderAdapter",
    "GenerationRequest",
    "GenerationResponse",
    "MediaProviderAdapter",
    "ProviderEndpoint",
    "ProviderTaskStatus",
    "provider_registry",
]

