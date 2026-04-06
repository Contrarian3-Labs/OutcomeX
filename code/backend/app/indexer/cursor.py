"""Cursor and idempotency stores for replayable indexing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.models import OnchainIndexerCursor, OnchainProcessedEvent


@dataclass(frozen=True)
class CursorState:
    chain_id: int
    last_indexed_block: int
    updated_at: datetime


class CursorStore(Protocol):
    def get(self, *, chain_id: int) -> CursorState | None:
        ...

    def set(self, *, chain_id: int, last_indexed_block: int) -> CursorState:
        ...


class ProcessedEventStore(Protocol):
    def contains(self, event_id: str) -> bool:
        ...

    def mark(self, event_id: str) -> None:
        ...


class InMemoryCursorStore:
    def __init__(self) -> None:
        self._states: dict[int, CursorState] = {}

    def get(self, *, chain_id: int) -> CursorState | None:
        return self._states.get(chain_id)

    def set(self, *, chain_id: int, last_indexed_block: int) -> CursorState:
        state = CursorState(
            chain_id=chain_id,
            last_indexed_block=last_indexed_block,
            updated_at=datetime.now(timezone.utc),
        )
        self._states[chain_id] = state
        return state


class InMemoryProcessedEventStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def contains(self, event_id: str) -> bool:
        return event_id in self._seen

    def mark(self, event_id: str) -> None:
        self._seen.add(event_id)


class SqlCursorStore:
    def __init__(self, *, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def get(self, *, chain_id: int) -> CursorState | None:
        with self._session_factory() as db:
            row = db.get(OnchainIndexerCursor, chain_id)
            if row is None:
                return None
            return CursorState(
                chain_id=row.chain_id,
                last_indexed_block=row.last_indexed_block,
                updated_at=row.updated_at,
            )

    def set(self, *, chain_id: int, last_indexed_block: int) -> CursorState:
        with self._session_factory() as db:
            row = db.get(OnchainIndexerCursor, chain_id)
            if row is None:
                row = OnchainIndexerCursor(chain_id=chain_id, last_indexed_block=last_indexed_block)
            else:
                row.last_indexed_block = last_indexed_block
            db.add(row)
            db.commit()
            db.refresh(row)
            return CursorState(
                chain_id=row.chain_id,
                last_indexed_block=row.last_indexed_block,
                updated_at=row.updated_at,
            )


class SqlProcessedEventStore:
    def __init__(self, *, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def contains(self, event_id: str) -> bool:
        with self._session_factory() as db:
            return db.get(OnchainProcessedEvent, event_id) is not None

    def mark(self, event_id: str) -> None:
        with self._session_factory() as db:
            row = db.get(OnchainProcessedEvent, event_id)
            if row is None:
                db.add(OnchainProcessedEvent(event_id=event_id))
                db.commit()
