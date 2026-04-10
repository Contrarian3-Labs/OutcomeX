from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_DB_PATH = BACKEND_ROOT / "outcomex.db"
DEFAULT_ENV_FILE_PATH = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    app_name: str = "OutcomeX Backend"
    app_version: str = "0.1.0"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = f"sqlite+pysqlite:///{DEFAULT_SQLITE_DB_PATH.as_posix()}"
    auto_create_tables: bool = True
    hsp_base_url: str = "https://mock-hsp.local"
    hsp_api_key: str = "dev-key"
    hsp_api_base_url: str = "https://merchant-qa.hashkeymerchant.com"
    hsp_app_key: str = ""
    hsp_app_secret: str = ""
    hsp_webhook_url: str = ""
    hsp_redirect_url: str = ""
    hsp_merchant_name: str = "OutcomeX"
    hsp_merchant_private_key_pem: str = ""
    hsp_network: str = "hashkey-testnet"
    hsp_pay_to_address: str = ""
    hsp_webhook_tolerance_seconds: int = 300
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com"
    dashscope_compatible_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    dashscope_text_model: str = "qwen3.6-plus"
    dashscope_image_model: str = "wan2.6-t2i"
    dashscope_video_model: str = "wan2.2-t2v-plus"
    dashscope_request_timeout_seconds: float = 120.0
    agentskillos_root: str = ""
    agentskillos_skill_group: str = "skill_seeds"
    agentskillos_llm_model: str = ""
    agentskillos_discovery_timeout_seconds: float = 120.0
    agentskillos_execution_mode: str = "dag"
    agentskillos_execution_timeout_seconds: float = 1800.0
    agentskillos_execution_output_root: str = "data/agentskillos-execution"
    buyer_wallet_map_json: str = "{}"
    user_signer_private_keys_json: str = "{}"
    onchain_chain_id: int = 133
    onchain_rpc_url: str = ""
    onchain_receipt_timeout_seconds: float = 10.0
    onchain_tx_timeout_seconds: float = 10.0
    onchain_order_book_address: str = "0x0000000000000000000000000000000000000133"
    onchain_order_payment_router_address: str = "0x0000000000000000000000000000000000000134"
    onchain_machine_asset_address: str = "0x0000000000000000000000000000000000000132"
    onchain_machine_marketplace_address: str = "0x0000000000000000000000000000000000000137"
    onchain_settlement_controller_address: str = "0x0000000000000000000000000000000000000135"
    onchain_revenue_vault_address: str = "0x0000000000000000000000000000000000000136"
    onchain_pwr_token_address: str = "0x0000000000000000000000000000000000000A11"
    onchain_permit2_address: str = "0x0000000000000000000000000000000000000A12"
    onchain_usdc_address: str = "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"
    onchain_usdt_address: str = "0x372325443233fEbaC1F6998aC750276468c83CC6"
    onchain_usdc_eip3009_name: str = "USD Coin"
    onchain_usdc_eip3009_version: str = "2"
    onchain_permit2_name: str = "Permit2"
    onchain_broadcaster_private_key: str = ""
    onchain_adapter_private_key: str = ""
    onchain_machine_owner_private_key: str = ""
    onchain_buyer_private_key: str = ""
    onchain_platform_treasury_private_key: str = ""
    onchain_indexer_enabled: bool = True
    onchain_indexer_poll_seconds: float = 2.0
    onchain_indexer_confirmation_depth: int = 0
    onchain_indexer_bootstrap_block: int = 0
    onchain_indexer_max_block_span: int = 2000
    execution_sync_enabled: bool = True
    execution_sync_poll_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_prefix="OUTCOMEX_",
        env_file=str(DEFAULT_ENV_FILE_PATH),
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
