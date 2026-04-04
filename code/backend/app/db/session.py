from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.container import get_container


def get_db_session() -> Generator[Session, None, None]:
    session = get_container().session_factory()
    try:
        yield session
    finally:
        session.close()

