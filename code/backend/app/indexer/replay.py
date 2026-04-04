"""Replay loop and idempotent event application skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from app.indexer.cursor import (
    CursorStore,
    InMemoryProcessedEventStore,
    ProcessedEventStore,
)
from app.indexer.events import try_normalize_decoded_event
from app.indexer.projections import ProjectionStore
from app.onchain.adapter import ChainAdapter, DecodedChainEvent


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
    reorg_detected: bool
    rewind_required_from_block: int | None


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
                reorg_detected=False,
                rewind_required_from_block=None,
            )

        decoded_events = sorted(
            self._adapter.iter_events(from_block=from_block, to_block=bounded_to_block),
            key=_event_sort_key,
        )
        scanned_events = len(decoded_events)
        applied_events = 0
        skipped_duplicates = 0
        skipped_removed = 0
        highest_seen_block = cursor.last_indexed_block if cursor else None
        rewind_required_from_block: int | None = None

        for decoded_event in decoded_events:
            highest_seen_block = (
                decoded_event.block_number
                if highest_seen_block is None
                else max(highest_seen_block, decoded_event.block_number)
            )

            if decoded_event.removed:
                skipped_removed += 1
                rewind_required_from_block = (
                    decoded_event.block_number
                    if rewind_required_from_block is None
                    else min(rewind_required_from_block, decoded_event.block_number)
                )

        safe_upper_block = (
            rewind_required_from_block - 1
            if rewind_required_from_block is not None
            else None
        )

        for decoded_event in decoded_events:
            if decoded_event.removed:
                continue
            if safe_upper_block is not None and decoded_event.block_number > safe_upper_block:
                continue

            normalized_event = try_normalize_decoded_event(decoded_event)
            if normalized_event is None:
                continue
            if self._processed_event_store.contains(normalized_event.event_id):
                skipped_duplicates += 1
                continue

            self._projection_store.apply(normalized_event)
            self._processed_event_store.mark(normalized_event.event_id)
            applied_events += 1

        next_cursor_block = highest_seen_block
        if safe_upper_block is not None and next_cursor_block is not None:
            next_cursor_block = min(next_cursor_block, safe_upper_block)
        if cursor is not None and next_cursor_block is not None:
            next_cursor_block = max(next_cursor_block, cursor.last_indexed_block)
        if next_cursor_block is not None:
            self._cursor_store.set(chain_id=chain_id, last_indexed_block=next_cursor_block)

        return ReplayOutcome(
            from_block=from_block,
            to_block=bounded_to_block,
            scanned_events=scanned_events,
            applied_events=applied_events,
            skipped_duplicates=skipped_duplicates,
            skipped_removed=skipped_removed,
            cursor_advanced_to=next_cursor_block,
            reorg_detected=rewind_required_from_block is not None,
            rewind_required_from_block=rewind_required_from_block,
        )

    def _apply_confirmation_depth(self, *, from_block: int, to_block: int | None) -> int | None:
        if to_block is None:
            return None
        safe_upper_bound = to_block - max(0, self._config.confirmation_depth)
        return max(from_block - 1, safe_upper_bound)


def _event_sort_key(event: DecodedChainEvent) -> tuple[int, int, str, str, str]:
    return (
        event.block_number,
        event.log_index,
        str(event.transaction_hash).lower(),
        str(event.contract_address).lower(),
        str(event.event_name),
    )
