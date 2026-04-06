from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.models import Base
from app.indexer.cursor import SqlCursorStore, SqlProcessedEventStore


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def test_sql_cursor_store_persists_last_indexed_block() -> None:
    session_factory = _session_factory()
    first_store = SqlCursorStore(session_factory=session_factory)
    first_store.set(chain_id=133, last_indexed_block=42)

    second_store = SqlCursorStore(session_factory=session_factory)
    state = second_store.get(chain_id=133)

    assert state is not None
    assert state.chain_id == 133
    assert state.last_indexed_block == 42


def test_sql_processed_event_store_marks_and_recalls_event_ids() -> None:
    session_factory = _session_factory()
    first_store = SqlProcessedEventStore(session_factory=session_factory)

    assert first_store.contains("event-1") is False
    first_store.mark("event-1")

    second_store = SqlProcessedEventStore(session_factory=session_factory)
    assert second_store.contains("event-1") is True
