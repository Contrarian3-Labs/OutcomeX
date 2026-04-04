from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OutcomeX Backend"
    app_version: str = "0.1.0"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite+pysqlite:///./outcomex.db"
    auto_create_tables: bool = True
    hsp_base_url: str = "https://mock-hsp.local"
    hsp_api_key: str = "dev-key"

    model_config = SettingsConfigDict(
        env_prefix="OUTCOMEX_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()

