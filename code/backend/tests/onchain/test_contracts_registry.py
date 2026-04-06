import pytest

from app.core.config import reset_settings_cache
from app.onchain.contracts_registry import ContractsRegistry


@pytest.fixture(autouse=True)
def _reset_settings_cache_between_tests():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_contracts_registry_defaults_match_previous_bootstrap_values() -> None:
    registry = ContractsRegistry()

    assert registry.order_book().chain_id == 133
    assert registry.order_book().contract_address == "0x0000000000000000000000000000000000000133"
    assert registry.payment_router().contract_address == "0x0000000000000000000000000000000000000134"
    assert registry.payment_token("USDC") == "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"
    assert registry.payment_token("USDT") == "0x372325443233febac1f6998ac750276468c83cc6"
    assert registry.payment_token("PWR") == "0x0000000000000000000000000000000000000a11"


def test_contracts_registry_reads_chain_targets_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_CHAIN_ID", "31337")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS", "0x1111111111111111111111111111111111111111")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_PAYMENT_ROUTER_ADDRESS", "0x2222222222222222222222222222222222222222")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS", "0x3333333333333333333333333333333333333333")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS", "0x4444444444444444444444444444444444444444")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS", "0x5555555555555555555555555555555555555555")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_PERMIT2_ADDRESS", "0x6666666666666666666666666666666666666666")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS", "0x7777777777777777777777777777777777777777")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_USDC_ADDRESS", "0x8888888888888888888888888888888888888888")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_USDT_ADDRESS", "0x9999999999999999999999999999999999999999")

    registry = ContractsRegistry()

    assert registry.order_book().chain_id == 31337
    assert registry.payment_router().contract_address == "0x2222222222222222222222222222222222222222"
    assert registry.machine_asset().contract_address == "0x3333333333333333333333333333333333333333"
    assert registry.settlement_controller().contract_address == "0x4444444444444444444444444444444444444444"
    assert registry.revenue_vault().contract_address == "0x5555555555555555555555555555555555555555"
    assert registry.permit2().contract_address == "0x6666666666666666666666666666666666666666"
    assert registry.pwr_token().contract_address == "0x7777777777777777777777777777777777777777"
    assert registry.payment_token("USDC") == "0x8888888888888888888888888888888888888888"
    assert registry.payment_token("USDT") == "0x9999999999999999999999999999999999999999"


def test_contracts_registry_rejects_invalid_address() -> None:
    with pytest.raises(ValueError, match="invalid_evm_address"):
        ContractsRegistry(order_book_address="not-an-address")
