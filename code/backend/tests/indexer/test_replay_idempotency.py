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

    def iter_events(self, *, from_block: int, to_block: int | None = None) -> Iterable[DecodedChainEvent]:
        self.last_from_block = from_block
        return list(self._events)


def _order_opened_event(*, block_number: int, log_index: int) -> DecodedChainEvent:
    return DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name="OrderOpened",
        block_number=block_number,
        block_hash=f"0xblock-{block_number}",
        transaction_hash="0xabc",
        log_index=log_index,
        args={
            "orderId": "ord_1",
            "machineId": "MA-001",
            "buyer": "0x9999999999999999999999999999999999999999",
            "amountWei": "250",
            "status": "OPEN",
        },
    )


def test_replay_indexer_applies_duplicate_log_once_and_advances_cursor() -> None:
    duplicate = _order_opened_event(block_number=10, log_index=7)
    adapter = StubChainAdapter([duplicate, duplicate, _order_opened_event(block_number=12, log_index=1)])
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
    assert projection.get_order("ord_1").status == "OPEN"
    assert cursor_store.get(chain_id=177).last_indexed_block == 12
