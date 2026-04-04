"""Core execution contracts for intent normalization and provider matching.

These shapes are intentionally lightweight and deterministic so backend-core can
call execution services without depending on provider details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaType(str, Enum):
    """Supported execution output modalities."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"


class MatchStatus(str, Enum):
    """Outcome of solution matching against provider capabilities."""

    MATCHED = "matched"
    FALLBACK = "fallback"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class ExecutionConstraints:
    """Optional controls provided by upstream intent parsing."""

    max_latency_ms: int = 20_000
    max_cost_usd: float = 2.0
    prefer_provider: str | None = None
    require_preview: bool = True


@dataclass(frozen=True)
class IntentRequest:
    """Normalized user intent before execution planning."""

    intent_id: str
    prompt: str
    desired_outputs: tuple[MediaType, ...] = (MediaType.TEXT,)
    constraints: ExecutionConstraints = field(default_factory=ExecutionConstraints)
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceEstimate:
    """Resource profile consumed by one step during runtime dispatch."""

    capacity_units: int
    memory_mb: int
    expected_duration_ticks: int


@dataclass(frozen=True)
class ExecutionStep:
    """One executable unit in a recipe."""

    step_id: str
    provider: str
    model: str
    action: str
    output_type: MediaType
    resources: ResourceEstimate
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRecipe:
    """A deterministic plan produced from an intent."""

    recipe_id: str
    source_intent_id: str
    prompt: str
    steps: tuple[ExecutionStep, ...]
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def total_capacity_units(self) -> int:
        """Aggregate capacity across all recipe steps."""
        return sum(step.resources.capacity_units for step in self.steps)

    @property
    def total_memory_mb(self) -> int:
        """Peak memory heuristic for MVP scheduling."""
        if not self.steps:
            return 0
        return max(step.resources.memory_mb for step in self.steps)


@dataclass(frozen=True)
class CandidateMatch:
    """Scored provider/model candidate for one recipe."""

    provider: str
    model_id: str
    action: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolutionMatchResult:
    """Final matchmaking output consumed by execution service."""

    status: MatchStatus
    selected: CandidateMatch | None
    alternatives: tuple[CandidateMatch, ...] = ()
    missing_requirements: tuple[str, ...] = ()

