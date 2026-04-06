import os

import pytest

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.onchain.lifecycle_service import reset_onchain_lifecycle_service_cache


@pytest.fixture(autouse=True)
def reset_cached_settings_and_container() -> None:
    os.environ["OUTCOMEX_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "false"
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()
    yield
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()

