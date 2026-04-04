from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.integrations.execution_gateway import NullExecutionGateway
from app.integrations.hsp_adapter import HSPAdapter
from app.integrations.onchain_indexer import NullOnchainIndexer


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
        # Extension point: swap with concrete on-chain indexer integration.
        self.onchain_indexer = NullOnchainIndexer()


@lru_cache
def get_container() -> Container:
    return Container(get_settings())


def reset_container_cache() -> None:
    get_container.cache_clear()

