from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.container import Container, get_container
from app.execution.service import ExecutionEngineService
from app.integrations.agentskillos_execution_service import (
    AgentSkillOSExecutionService,
    get_agentskillos_execution_service,
)


def get_dependency_container() -> Container:
    return get_container()


def get_db() -> Generator[Session, None, None]:
    session = get_container().session_factory()
    try:
        yield session
    finally:
        session.close()


def get_execution_engine_service(
    *,
    hardware_simulator=None,
    execution_service: AgentSkillOSExecutionService | None = None,
) -> ExecutionEngineService:
    container = get_container()
    return ExecutionEngineService(
        hardware_simulator=hardware_simulator or container.hardware_simulator,
        execution_service=execution_service or get_agentskillos_execution_service(),
    )

