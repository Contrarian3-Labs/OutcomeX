import os

from app.indexer.evm_runtime import build_subscriptions_from_env


def test_build_subscriptions_reads_onchain_address_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS", "0x0000000000000000000000000000000000000132")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS", "0x0000000000000000000000000000000000000133")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS", "0x0000000000000000000000000000000000000135")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS", "0x0000000000000000000000000000000000000136")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS", "0x0000000000000000000000000000000000000a11")

    subscriptions = build_subscriptions_from_env()

    assert subscriptions
    assert any(item.contract_name == "OrderBook" for item in subscriptions)
    assert any(item.contract_name == "MachineAssetNFT" for item in subscriptions)
    assert all(item.contract_address.startswith("0x") for item in subscriptions)
