from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.integrations.execution_gateway import NullExecutionGateway
from app.integrations.hsp_adapter import HSPAdapter
from app.integrations.onchain_indexer import create_onchain_indexer
from app.integrations.user_signer_registry import UserSignerRegistry
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.healthcheck import OnchainHealthChecker
from app.runtime.hardware_simulator import get_shared_hardware_simulator, reset_shared_hardware_simulator


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Container:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ensure_sqlite_parent_directory(settings.database_url)
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
            api_base_url=settings.hsp_api_base_url,
            app_key=settings.hsp_app_key,
            app_secret=settings.hsp_app_secret,
            merchant_name=settings.hsp_merchant_name,
            merchant_private_key_pem=settings.hsp_merchant_private_key_pem,
            network=settings.hsp_network,
            chain_id=settings.onchain_chain_id,
            pay_to_address=settings.hsp_pay_to_address,
            redirect_url=settings.hsp_redirect_url,
            webhook_tolerance_seconds=settings.hsp_webhook_tolerance_seconds,
            supported_currencies=settings.hsp_supported_currencies,
            usdc_address=settings.onchain_usdc_address,
            usdt_address=settings.onchain_usdt_address,
        )
        self.buyer_address_resolver = BuyerAddressResolver.from_json(settings.buyer_wallet_map_json)
        self.user_signer_registry = UserSignerRegistry.from_json(settings.user_signer_private_keys_json)
        # Extension point: swap with concrete execution integration.
        self.execution_gateway = NullExecutionGateway()
        self.hardware_simulator = get_shared_hardware_simulator()
        self.contracts_registry = ContractsRegistry(settings=settings)
        self.onchain_health_checker = OnchainHealthChecker(settings=settings)
        self.onchain_health_report = self.onchain_health_checker.run()
        self.onchain_indexer = create_onchain_indexer(
            session_factory=self.session_factory,
            owner_resolver=self.buyer_address_resolver.resolve_user_id,
            settings=self.settings,
        )

    @staticmethod
    def _ensure_sqlite_parent_directory(database_url: str) -> None:
        try:
            url = make_url(database_url)
        except Exception:
            return
        if not url.drivername.startswith("sqlite"):
            return
        if not url.database or url.database == ":memory:":
            return
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_container() -> Container:
    return Container(get_settings())


def reset_container_cache() -> None:
    get_container.cache_clear()
    reset_shared_hardware_simulator()
