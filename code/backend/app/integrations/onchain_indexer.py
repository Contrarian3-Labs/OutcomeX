from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings
from sqlalchemy.orm import sessionmaker

from app.indexer.cursor import SqlCursorStore, SqlProcessedEventStore
from app.indexer.evm_runtime import (
    Web3AbiEventDecoder,
    build_subscriptions,
    load_runtime_config,
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


def create_onchain_indexer(
    *,
    session_factory: sessionmaker,
    owner_resolver=None,
    settings: Settings | None = None,
) -> OnchainIndexer:
    resolved = settings or get_settings()
    if not resolved.onchain_indexer_enabled:
        return NullOnchainIndexer(reason="indexer_disabled")

    runtime = load_runtime_config(resolved)
    if not runtime.rpc_url:
        return NullOnchainIndexer(reason="rpc_url_missing")

    try:
        subscriptions = build_subscriptions(resolved)
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
        cursor_store=SqlCursorStore(session_factory=session_factory),
        processed_event_store=SqlProcessedEventStore(session_factory=session_factory),
        config=IndexerConfig(
            confirmation_depth=max(0, runtime.confirmation_depth),
            bootstrap_block=max(0, runtime.bootstrap_block),
        ),
    )
    return RpcPollingOnchainIndexer(replay_indexer=replay_indexer, chain_id=runtime.chain_id)


def get_onchain_indexer_poll_seconds(default: float = 2.0, *, settings: Settings | None = None) -> float:
    value = (settings or get_settings()).onchain_indexer_poll_seconds
    return value if value > 0 else default


def _is_indexer_enabled() -> bool:
    return get_settings().onchain_indexer_enabled
