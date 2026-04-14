from app.core.config import Settings
from app.indexer.evm_runtime import build_subscriptions, load_runtime_config


def test_hashkey_runtime_config_uses_chain_rpc_and_contract_addresses() -> None:
    settings = Settings(
        onchain_chain_id=133,
        onchain_rpc_url="https://testnet.hsk.xyz",
        onchain_machine_asset_address="0x0000000000000000000000000000000000000132",
        onchain_machine_marketplace_address="0x0000000000000000000000000000000000000137",
        onchain_order_book_address="0x0000000000000000000000000000000000000133",
        onchain_order_payment_router_address="0x0000000000000000000000000000000000000134",
        onchain_settlement_controller_address="0x0000000000000000000000000000000000000135",
        onchain_revenue_vault_address="0x0000000000000000000000000000000000000136",
        onchain_pwr_token_address="0x0000000000000000000000000000000000000A11",
        onchain_indexer_poll_seconds=3.0,
        onchain_indexer_confirmation_depth=4,
        onchain_indexer_bootstrap_block=123,
        onchain_indexer_max_block_span=500,
    )

    runtime = load_runtime_config(settings)
    subscriptions = build_subscriptions(settings)

    assert runtime.chain_id == 133
    assert runtime.rpc_url == "https://testnet.hsk.xyz"
    assert runtime.poll_seconds == 3.0
    assert runtime.confirmation_depth == 4
    assert runtime.bootstrap_block == 123
    assert runtime.max_block_span == 500
    assert {subscription.contract_name for subscription in subscriptions} == {
        "MachineAssetNFT",
        "MachineMarketplace",
        "OrderBook",
        "OrderPaymentRouter",
        "SettlementController",
        "RevenueVault",
        "PWRToken",
    }
