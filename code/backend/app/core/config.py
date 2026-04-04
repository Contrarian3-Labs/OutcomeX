from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SQLITE_DB_PATH = Path(__file__).resolve().parents[2] / "outcomex.db"


class Settings(BaseSettings):
    app_name: str = "OutcomeX Backend"
    app_version: str = "0.1.0"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = f"sqlite+pysqlite:///{DEFAULT_SQLITE_DB_PATH.as_posix()}"
    auto_create_tables: bool = True
    hsp_base_url: str = "https://mock-hsp.local"
    hsp_api_key: str = "dev-key"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com"
    dashscope_compatible_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    dashscope_text_model: str = "qwen3.6-plus"
    dashscope_image_model: str = "wan2.6-t2i"
    dashscope_video_model: str = "wan2.2-t2v-plus"
    dashscope_request_timeout_seconds: float = 120.0

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
