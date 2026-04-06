from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.models import Base
from app.indexer.cursor import SqlCursorStore, SqlProcessedEventStore
from app.integrations.onchain_indexer import RpcPollingOnchainIndexer, create_onchain_indexer


def test_create_onchain_indexer_uses_sql_backed_state_stores(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "http://rpc.local")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_CHAIN_ID", "133")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS", "0x1000000000000000000000000000000000000001")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS", "0x1000000000000000000000000000000000000002")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS", "0x1000000000000000000000000000000000000003")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS", "0x1000000000000000000000000000000000000004")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS", "0x1000000000000000000000000000000000000005")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    class _AdapterStub:
        chain_id = 133

    indexer_module = __import__("app.integrations.onchain_indexer", fromlist=["Web3ChainAdapter", "build_subscriptions_from_env"])
    monkeypatch.setattr(indexer_module, "build_subscriptions_from_env", lambda: [object()])
    monkeypatch.setattr(indexer_module.Web3ChainAdapter, "from_rpc_url", lambda **kwargs: _AdapterStub())

    indexer = create_onchain_indexer(session_factory=session_factory)

    assert isinstance(indexer, RpcPollingOnchainIndexer)
    assert isinstance(indexer._replay_indexer._cursor_store, SqlCursorStore)
    assert isinstance(indexer._replay_indexer._processed_event_store, SqlProcessedEventStore)
