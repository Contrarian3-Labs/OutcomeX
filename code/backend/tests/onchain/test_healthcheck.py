from __future__ import annotations

import os

from app.core.config import Settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.integrations.user_signer_registry import UserSignerRegistry
from app.onchain.healthcheck import OnchainHealthChecker

_PRIVATE_KEY_1 = "0x59c6995e998f97a5a004497e5daef9b27d5bffb6b82d49f68308fa6b0f68b7c0"
_PRIVATE_KEY_2 = "0x8b3a350cf5c34c9194ca3a545d1c5f4f7f7f4d87e5e865fe9d5efda5e0f3d2f7"


class _RpcStub:
    def __init__(self, *, chain_id_hex: str = "0x539", code_by_address: dict[str, str] | None = None) -> None:
        self.chain_id_hex = chain_id_hex
        self.code_by_address = {str(key).lower(): value for key, value in (code_by_address or {}).items()}
        self.calls: list[tuple[str, list[object]]] = []

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        if method == "eth_chainId":
            return self.chain_id_hex
        if method == "eth_getCode":
            return self.code_by_address.get(str(params[0]).lower(), "0x")
        raise AssertionError(f"unexpected_rpc_method:{method}")


def _settings() -> Settings:
    return Settings(
        env="test",
        onchain_chain_id=133,
        onchain_rpc_url="http://rpc.local",
        onchain_order_book_address="0x1000000000000000000000000000000000000001",
        onchain_order_payment_router_address="0x1000000000000000000000000000000000000002",
        onchain_machine_asset_address="0x1000000000000000000000000000000000000003",
        onchain_settlement_controller_address="0x1000000000000000000000000000000000000004",
        onchain_revenue_vault_address="0x1000000000000000000000000000000000000005",
        onchain_pwr_token_address="0x1000000000000000000000000000000000000006",
        onchain_usdc_address="0x1000000000000000000000000000000000000007",
        onchain_usdt_address="0x1000000000000000000000000000000000000008",
        onchain_broadcaster_private_key=_PRIVATE_KEY_1,
        user_signer_private_keys_json=os.environ.get("OUTCOMEX_USER_SIGNER_PRIVATE_KEYS_JSON", "{}"),
        buyer_wallet_map_json=os.environ.get("OUTCOMEX_BUYER_WALLET_MAP_JSON", "{}"),
    )


def test_healthcheck_reports_chain_id_mismatch() -> None:
    settings = _settings()
    rpc = _RpcStub(chain_id_hex="0x1")

    report = OnchainHealthChecker(settings=settings, rpc_client=rpc).run()

    assert report.healthy is False
    assert report.rpc_reachable is True
    assert report.chain_id_expected == 133
    assert report.chain_id_actual == 1
    assert any(error.startswith("chain_id_mismatch:") for error in report.errors)


def test_healthcheck_reports_user_signer_wallet_mismatch() -> None:
    settings = _settings().model_copy(
        update={
            "buyer_wallet_map_json": '{"buyer-1":"0x90f79bf6eb2c4f870365e785982e1f101e93b906"}',
            "user_signer_private_keys_json": '{"buyer-1":"%s"}' % _PRIVATE_KEY_2,
        }
    )

    report = OnchainHealthChecker(settings=settings, rpc_client=_RpcStub()).run()

    assert report.healthy is False
    assert any(error.startswith("user_signer_wallet_mismatch:buyer-1:") for error in report.errors)
    assert report.identity_mappings[0]["user_id"] == "buyer-1"
    assert report.identity_mappings[0]["matches"] is False


def test_healthcheck_reports_contract_code_presence_and_signer_addresses() -> None:
    settings = _settings().model_copy(
        update={
            "buyer_wallet_map_json": '{"buyer-1":"0xbb5936a4c114449ba8a84490e61dc5533a4bb8b2"}',
            "user_signer_private_keys_json": '{"buyer-1":"%s"}' % _PRIVATE_KEY_1,
        }
    )
    rpc = _RpcStub(
        code_by_address={
            settings.onchain_order_book_address: "0x6001",
            settings.onchain_order_payment_router_address: "0x6002",
            settings.onchain_machine_asset_address: "0x6003",
            settings.onchain_settlement_controller_address: "0x6004",
            settings.onchain_revenue_vault_address: "0x6005",
            settings.onchain_pwr_token_address: "0x6006",
            settings.onchain_usdc_address: "0x6007",
            settings.onchain_usdt_address: "0x6008",
        }
    )

    report = OnchainHealthChecker(settings=settings, rpc_client=rpc).run()

    assert report.rpc_reachable is True
    assert report.contracts["order_book"]["has_code"] is True
    assert report.contracts["usdt"]["has_code"] is True
    assert report.signers["broadcaster"]["wallet_address"] == "0xbb5936a4c114449ba8a84490e61dc5533a4bb8b2"
    assert report.identity_mappings[0]["expected_wallet_address"] == "0xbb5936a4c114449ba8a84490e61dc5533a4bb8b2"
