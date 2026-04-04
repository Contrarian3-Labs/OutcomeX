from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.container import Container, get_container


def get_dependency_container() -> Container:
    return get_container()


def get_db() -> Generator[Session, None, None]:
    session = get_container().session_factory()
    try:
        yield session
    finally:
        session.close()

