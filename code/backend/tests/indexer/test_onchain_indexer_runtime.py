from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.integrations.onchain_indexer import (
    NullOnchainIndexer,
    RpcPollingOnchainIndexer,
    create_onchain_indexer,
    get_onchain_indexer_poll_seconds,
)
from app.indexer.replay import ReplayOutcome


class StubReplayIndexer:
    def __init__(self, outcome: ReplayOutcome) -> None:
        self.outcome = outcome
        self.calls: list[int] = []

    def replay_once(self, *, chain_id: int, to_block=None) -> ReplayOutcome:
        to_block
        self.calls.append(chain_id)
        return self.outcome


def test_rpc_polling_onchain_indexer_calls_replay_once_with_chain_id() -> None:
    expected = ReplayOutcome(
        from_block=0,
        to_block=10,
        scanned_events=1,
        applied_events=1,
        skipped_duplicates=0,
        skipped_removed=0,
        cursor_advanced_to=10,
        reorg_detected=False,
        rewind_required_from_block=None,
    )
    replay = StubReplayIndexer(outcome=expected)
    indexer = RpcPollingOnchainIndexer(replay_indexer=replay, chain_id=133)

    outcome = indexer.poll_once()

    assert replay.calls == [133]
    assert outcome == expected


def test_null_onchain_indexer_returns_none_poll_outcome() -> None:
    indexer = NullOnchainIndexer(reason="disabled-for-test")

    assert indexer.poll_once() is None
    assert indexer.status.enabled is False
    assert indexer.status.reason == "disabled-for-test"


def test_get_onchain_indexer_poll_seconds_uses_default_for_non_positive_settings_value() -> None:
    settings = Settings(onchain_indexer_poll_seconds=0)
    assert get_onchain_indexer_poll_seconds(default=3.5, settings=settings) == 3.5


def test_create_onchain_indexer_returns_null_when_rpc_missing() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    settings = Settings(onchain_rpc_url="")

    indexer = create_onchain_indexer(session_factory=session_factory, settings=settings)

    assert isinstance(indexer, NullOnchainIndexer)
    assert indexer.status.reason == "rpc_url_missing"
