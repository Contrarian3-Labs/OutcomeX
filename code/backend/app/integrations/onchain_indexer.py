from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import sessionmaker

from app.indexer.cursor import InMemoryCursorStore, InMemoryProcessedEventStore
from app.indexer.evm_runtime import (
    Web3AbiEventDecoder,
    build_subscriptions_from_env,
    load_runtime_config_from_env,
)
from app.indexer.replay import IndexerConfig, ReplayIndexer, ReplayOutcome
from app.indexer.sql_projection import SqlProjectionStore
from app.onchain.adapter import Web3ChainAdapter


class OnchainIndexer(Protocol):
    def mark_settlement_distributed(self, settlement_id: str) -> None:
        ...

    def poll_once(self) -> ReplayOutcome | None:
        ...


@dataclass(frozen=True)
class OnchainIndexerStatus:
    enabled: bool
    reason: str


class NullOnchainIndexer:
    """No-op indexer used when RPC runtime is unavailable."""

    def __init__(self, reason: str = "disabled") -> None:
        self.status = OnchainIndexerStatus(enabled=False, reason=reason)

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        settlement_id
        return None

    def poll_once(self) -> ReplayOutcome | None:
        return None


class RpcPollingOnchainIndexer:
    """Live replay indexer driven by chain RPC polling."""

    def __init__(self, *, replay_indexer: ReplayIndexer, chain_id: int) -> None:
        self._replay_indexer = replay_indexer
        self._chain_id = chain_id
        self.status = OnchainIndexerStatus(enabled=True, reason="enabled")

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        settlement_id
        return None

    def poll_once(self) -> ReplayOutcome | None:
        return self._replay_indexer.replay_once(chain_id=self._chain_id)


def create_onchain_indexer(*, session_factory: sessionmaker, owner_resolver=None) -> OnchainIndexer:
    if not _is_indexer_enabled():
        return NullOnchainIndexer(reason="indexer_disabled")

    runtime = load_runtime_config_from_env()
    if not runtime.rpc_url:
        return NullOnchainIndexer(reason="rpc_url_missing")

    try:
        subscriptions = build_subscriptions_from_env()
        if not subscriptions:
            return NullOnchainIndexer(reason="subscriptions_missing")
        adapter = Web3ChainAdapter.from_rpc_url(
            rpc_url=runtime.rpc_url,
            chain_id=runtime.chain_id,
            subscriptions=subscriptions,
            decoder=Web3AbiEventDecoder(),
            max_block_span=runtime.max_block_span,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return NullOnchainIndexer(reason=f"adapter_init_failed:{exc.__class__.__name__}")

    replay_indexer = ReplayIndexer(
        adapter=adapter,
        projection_store=SqlProjectionStore(session_factory=session_factory, owner_resolver=owner_resolver),
        cursor_store=InMemoryCursorStore(),
        processed_event_store=InMemoryProcessedEventStore(),
        config=IndexerConfig(
            confirmation_depth=max(0, runtime.confirmation_depth),
            bootstrap_block=max(0, runtime.bootstrap_block),
        ),
    )
    return RpcPollingOnchainIndexer(replay_indexer=replay_indexer, chain_id=runtime.chain_id)


def get_onchain_indexer_poll_seconds(default: float = 2.0) -> float:
    raw = os.getenv("OUTCOMEX_ONCHAIN_INDEXER_POLL_SECONDS", str(default))
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _is_indexer_enabled() -> bool:
    raw = os.getenv("OUTCOMEX_ONCHAIN_INDEXER_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "off", "no"}

