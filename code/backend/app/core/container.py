from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.integrations.execution_gateway import NullExecutionGateway
from app.integrations.hsp_adapter import HSPAdapter
from app.integrations.onchain_indexer import NullOnchainIndexer
from app.runtime.hardware_simulator import get_shared_hardware_simulator, reset_shared_hardware_simulator


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Container:
    def __init__(self, settings: Settings):
        self.settings = settings
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self.engine = create_engine(
            settings.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if settings.database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            future=True,
        )
        self.hsp_adapter = HSPAdapter(
            base_url=settings.hsp_base_url,
            api_key=settings.hsp_api_key,
        )
        # Extension point: swap with concrete execution integration.
        self.execution_gateway = NullExecutionGateway()
        self.hardware_simulator = get_shared_hardware_simulator()
        # Extension point: swap with concrete on-chain indexer integration.
        self.onchain_indexer = NullOnchainIndexer()


@lru_cache
def get_container() -> Container:
    return Container(get_settings())


def reset_container_cache() -> None:
    get_container.cache_clear()
    reset_shared_hardware_simulator()
