"""Provider adapter primitives.

The response envelope is inspired by mulerouter-skills APIResponse/TaskResult to
keep downstream execution code provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from ...execution.contracts import MediaType


class ProviderTaskStatus(str, Enum):
    """Lifecycle status for provider generation requests."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class GenerationRequest:
    """Provider generation request payload."""

    prompt: str
    output_type: MediaType
    model_id: str
    action: str = "generation"
    negative_prompt: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResponse:
    """Normalized provider generation response."""

    success: bool
    provider: str
    status: ProviderTaskStatus
    task_id: str | None = None
    result_urls: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MediaProviderAdapter(Protocol):
    """Adapter protocol used by execution service."""

    provider_name: str

    def submit_generation(self, request: GenerationRequest) -> GenerationResponse:
        """Submit a generation request to provider."""

    def poll_generation(self, task_id: str, *, model_id: str, action: str) -> GenerationResponse:
        """Poll existing generation request by task id."""

