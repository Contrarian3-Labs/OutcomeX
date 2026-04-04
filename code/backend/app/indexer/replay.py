"""Replay loop and idempotent event application skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from app.indexer.cursor import (
    CursorStore,
    InMemoryProcessedEventStore,
    ProcessedEventStore,
)
from app.indexer.events import normalize_decoded_event
from app.indexer.projections import ProjectionStore
from app.onchain.adapter import ChainAdapter


@dataclass(frozen=True)
class IndexerConfig:
    confirmation_depth: int = 6
    bootstrap_block: int = 0


@dataclass(frozen=True)
class ReplayOutcome:
    from_block: int
    to_block: int | None
    scanned_events: int
    applied_events: int
    skipped_duplicates: int
    skipped_removed: int
    cursor_advanced_to: int | None


class ReplayIndexer:
    def __init__(
        self,
        *,
        adapter: ChainAdapter,
        projection_store: ProjectionStore,
        cursor_store: CursorStore,
        processed_event_store: ProcessedEventStore | None = None,
        config: IndexerConfig | None = None,
    ) -> None:
        self._adapter = adapter
        self._projection_store = projection_store
        self._cursor_store = cursor_store
        self._processed_event_store = processed_event_store or InMemoryProcessedEventStore()
        self._config = config or IndexerConfig()

    def replay_once(self, *, chain_id: int, to_block: int | None = None) -> ReplayOutcome:
        cursor = self._cursor_store.get(chain_id=chain_id)
        from_block = (
            self._config.bootstrap_block
            if cursor is None
            else cursor.last_indexed_block + 1
        )
        bounded_to_block = self._apply_confirmation_depth(from_block=from_block, to_block=to_block)
        if bounded_to_block is not None and bounded_to_block < from_block:
            return ReplayOutcome(
                from_block=from_block,
                to_block=bounded_to_block,
                scanned_events=0,
                applied_events=0,
                skipped_duplicates=0,
                skipped_removed=0,
                cursor_advanced_to=cursor.last_indexed_block if cursor else None,
            )

        scanned_events = 0
        applied_events = 0
        skipped_duplicates = 0
        skipped_removed = 0
        highest_seen_block = cursor.last_indexed_block if cursor else None

        for decoded_event in self._adapter.iter_events(from_block=from_block, to_block=bounded_to_block):
            scanned_events += 1
            highest_seen_block = (
                decoded_event.block_number
                if highest_seen_block is None
                else max(highest_seen_block, decoded_event.block_number)
            )

            if decoded_event.removed:
                skipped_removed += 1
                continue

            normalized_event = normalize_decoded_event(decoded_event)
            if self._processed_event_store.contains(normalized_event.event_id):
                skipped_duplicates += 1
                continue

            self._projection_store.apply(normalized_event)
            self._processed_event_store.mark(normalized_event.event_id)
            applied_events += 1

        if highest_seen_block is not None:
            self._cursor_store.set(chain_id=chain_id, last_indexed_block=highest_seen_block)

        return ReplayOutcome(
            from_block=from_block,
            to_block=bounded_to_block,
            scanned_events=scanned_events,
            applied_events=applied_events,
            skipped_duplicates=skipped_duplicates,
            skipped_removed=skipped_removed,
            cursor_advanced_to=highest_seen_block,
        )

    def _apply_confirmation_depth(self, *, from_block: int, to_block: int | None) -> int | None:
        if to_block is None:
            return None
        safe_upper_bound = to_block - max(0, self._config.confirmation_depth)
        return max(from_block - 1, safe_upper_bound)
