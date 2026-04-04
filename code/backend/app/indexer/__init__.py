"""OutcomeX on-chain indexer package."""

from .cursor import CursorState, InMemoryCursorStore, InMemoryProcessedEventStore
from .events import (
    MachineAssetEvent,
    NormalizedEvent,
    OrderLifecycleEvent,
    PWRMintedEvent,
    RevenueClaimedEvent,
    SettlementSplitEvent,
    TransferGuardUpdatedEvent,
    normalize_decoded_event,
    try_normalize_decoded_event,
)
from .projections import InMemoryProjectionStore
from .replay import IndexerConfig, ReplayIndexer, ReplayOutcome

__all__ = [
    "CursorState",
    "InMemoryCursorStore",
    "InMemoryProcessedEventStore",
    "MachineAssetEvent",
    "NormalizedEvent",
    "OrderLifecycleEvent",
    "PWRMintedEvent",
    "RevenueClaimedEvent",
    "SettlementSplitEvent",
    "TransferGuardUpdatedEvent",
    "normalize_decoded_event",
    "try_normalize_decoded_event",
    "InMemoryProjectionStore",
    "IndexerConfig",
    "ReplayIndexer",
    "ReplayOutcome",
]
