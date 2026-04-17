"""Tests for replay idempotency behavior."""

from __future__ import annotations

import pathlib
import sys
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.indexer.cursor import InMemoryCursorStore
from app.indexer.events import normalize_decoded_event
from app.indexer.projections import InMemoryProjectionStore
from app.indexer.replay import IndexerConfig, ReplayIndexer
from app.onchain.adapter import ChainAdapter, DecodedChainEvent


class StubChainAdapter(ChainAdapter):
    def __init__(self, events: list[DecodedChainEvent]) -> None:
        self._events = events
        self.last_from_block: int | None = None
        self.head_block: int = 0

    def latest_block(self) -> int:
        return self.head_block

    def iter_events(self, *, from_block: int, to_block: int | None = None) -> Iterable[DecodedChainEvent]:
        self.last_from_block = from_block
        return list(self._events)


def _order_event(
    *,
    block_number: int,
    log_index: int,
    event_name: str = "OrderCreated",
    transaction_hash: str = "0xabc",
    removed: bool = False,
) -> DecodedChainEvent:
    args = {
        "orderId": "1",
        "machineId": "1",
        "buyer": "0x9999999999999999999999999999999999999999",
        "grossAmount": "250",
    }
    if event_name == "OrderPaid":
        args = {"orderId": "1", "machineId": "1", "grossAmount": "250"}
    elif event_name == "PreviewReady":
        args = {"orderId": "1", "machineId": "1", "validPreview": True}
    elif event_name == "OrderSettled":
        args = {
            "orderId": "1",
            "machineId": "1",
            "kind": 0,
            "refundToBuyer": "0",
            "platformShare": "25",
            "machineShare": "225",
            "dividendEligible": True,
        }

    return DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name=event_name,
        block_number=block_number,
        block_hash=f"0xblock-{block_number}",
        transaction_hash=transaction_hash,
        log_index=log_index,
        args=args,
        removed=removed,
    )


def test_replay_indexer_applies_duplicate_log_once_and_advances_cursor() -> None:
    duplicate = _order_event(block_number=10, log_index=7)
    adapter = StubChainAdapter([duplicate, duplicate, _order_event(block_number=12, log_index=1)])
    cursor_store = InMemoryCursorStore()
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=0),
    )

    indexer.replay_once(chain_id=177, to_block=20)

    assert adapter.last_from_block == 0
    assert projection.applied_event_ids == [
        normalize_decoded_event(duplicate).event_id,
        "177:12:0xabc:1",
    ]
    assert projection.get_order("1").status == "CREATED"
    assert cursor_store.get(chain_id=177).last_indexed_block == 20


def test_replay_indexer_applies_events_in_chain_log_order() -> None:
    block_20_late = _order_event(
        block_number=20,
        log_index=9,
        event_name="OrderPaid",
        transaction_hash="0xbbb",
    )
    block_20_early = _order_event(
        block_number=20,
        log_index=1,
        event_name="OrderCreated",
        transaction_hash="0xaaa",
    )
    block_21 = _order_event(
        block_number=21,
        log_index=0,
        event_name="PreviewReady",
        transaction_hash="0xccc",
    )
    adapter = StubChainAdapter([block_20_late, block_21, block_20_early])
    cursor_store = InMemoryCursorStore()
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=0),
    )

    indexer.replay_once(chain_id=177, to_block=30)

    assert projection.applied_event_ids == [
        "177:20:0xaaa:1",
        "177:20:0xbbb:9",
        "177:21:0xccc:0",
    ]


def test_replay_indexer_stops_at_removed_logs_and_flags_rewind() -> None:
    safe_event = _order_event(block_number=101, log_index=0, transaction_hash="0x101")
    removed_event = _order_event(
        block_number=103,
        log_index=0,
        transaction_hash="0x103",
        removed=True,
    )
    unsafe_event = _order_event(
        block_number=104,
        log_index=0,
        event_name="OrderSettled",
        transaction_hash="0x104",
    )
    adapter = StubChainAdapter([unsafe_event, removed_event, safe_event])
    cursor_store = InMemoryCursorStore()
    cursor_store.set(chain_id=177, last_indexed_block=100)
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=0),
    )

    outcome = indexer.replay_once(chain_id=177, to_block=200)

    assert projection.applied_event_ids == ["177:101:0x101:0"]
    assert outcome.cursor_advanced_to == 102
    assert cursor_store.get(chain_id=177).last_indexed_block == 102
    assert outcome.skipped_removed == 1
    assert outcome.reorg_detected is True
    assert outcome.rewind_required_from_block == 103


def test_replay_indexer_skips_unsupported_events_without_failing() -> None:
    supported = _order_event(block_number=11, log_index=1, event_name="OrderCreated", transaction_hash="0x111")
    unsupported = _order_event(
        block_number=12,
        log_index=2,
        event_name="SettlementControllerSet",
        transaction_hash="0x222",
    )
    adapter = StubChainAdapter([unsupported, supported])
    cursor_store = InMemoryCursorStore()
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=0),
    )

    outcome = indexer.replay_once(chain_id=177, to_block=20)

    assert projection.applied_event_ids == ["177:11:0x111:1"]
    assert outcome.applied_events == 1
    assert cursor_store.get(chain_id=177).last_indexed_block == 20


def test_replay_indexer_advances_cursor_across_empty_ranges() -> None:
    adapter = StubChainAdapter([])
    cursor_store = InMemoryCursorStore()
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=0),
    )

    outcome = indexer.replay_once(chain_id=177, to_block=42)

    assert outcome.scanned_events == 0
    assert outcome.cursor_advanced_to == 42
    assert cursor_store.get(chain_id=177).last_indexed_block == 42


def test_replay_indexer_uses_adapter_head_with_confirmation_depth_when_to_block_is_omitted() -> None:
    adapter = StubChainAdapter([])
    adapter.head_block = 50
    cursor_store = InMemoryCursorStore()
    projection = InMemoryProjectionStore()
    indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=projection,
        cursor_store=cursor_store,
        config=IndexerConfig(confirmation_depth=2),
    )

    outcome = indexer.replay_once(chain_id=177)

    assert outcome.to_block == 48
    assert outcome.cursor_advanced_to == 48
    assert cursor_store.get(chain_id=177).last_indexed_block == 48
