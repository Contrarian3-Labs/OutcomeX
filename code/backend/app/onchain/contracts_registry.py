from __future__ import annotations

from dataclasses import dataclass
import re

from app.core.config import Settings, get_settings

_EVM_ADDRESS_RE = re.compile(r"^0x[a-f0-9]{40}$")


@dataclass(frozen=True)
class ContractTarget:
    chain_id: int
    contract_name: str
    contract_address: str


class ContractsRegistry:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        chain_id: int | None = None,
        order_book_address: str | None = None,
        order_payment_router_address: str | None = None,
        machine_asset_address: str | None = None,
        machine_marketplace_address: str | None = None,
        settlement_controller_address: str | None = None,
        revenue_vault_address: str | None = None,
        pwr_token_address: str | None = None,
        permit2_address: str | None = None,
        usdc_address: str | None = None,
        usdt_address: str | None = None,
        pwr_address: str | None = None,
    ) -> None:
        runtime_settings = settings or get_settings()
        resolved_chain_id = chain_id if chain_id is not None else runtime_settings.onchain_chain_id

        resolved_order_book = order_book_address or runtime_settings.onchain_order_book_address
        resolved_router = order_payment_router_address or runtime_settings.onchain_order_payment_router_address
        resolved_machine_asset = machine_asset_address or runtime_settings.onchain_machine_asset_address
        resolved_machine_marketplace = machine_marketplace_address or runtime_settings.onchain_machine_marketplace_address
        resolved_settlement = settlement_controller_address or runtime_settings.onchain_settlement_controller_address
        resolved_revenue_vault = revenue_vault_address or runtime_settings.onchain_revenue_vault_address
        resolved_pwr_token = pwr_token_address or runtime_settings.onchain_pwr_token_address
        resolved_permit2 = permit2_address or runtime_settings.onchain_permit2_address

        resolved_usdc = usdc_address or runtime_settings.onchain_usdc_address
        resolved_usdt = usdt_address or runtime_settings.onchain_usdt_address
        resolved_pwr = pwr_address or runtime_settings.onchain_pwr_token_address

        self._targets = {
            "OrderBook": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="OrderBook",
                contract_address=_normalize_address(resolved_order_book),
            ),
            "OrderPaymentRouter": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="OrderPaymentRouter",
                contract_address=_normalize_address(resolved_router),
            ),
            "MachineAssetNFT": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="MachineAssetNFT",
                contract_address=_normalize_address(resolved_machine_asset),
            ),
            "MachineMarketplace": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="MachineMarketplace",
                contract_address=_normalize_address(resolved_machine_marketplace),
            ),
            "SettlementController": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="SettlementController",
                contract_address=_normalize_address(resolved_settlement),
            ),
            "RevenueVault": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="RevenueVault",
                contract_address=_normalize_address(resolved_revenue_vault),
            ),
            "PWRToken": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="PWRToken",
                contract_address=_normalize_address(resolved_pwr_token),
            ),
            "Permit2": ContractTarget(
                chain_id=resolved_chain_id,
                contract_name="Permit2",
                contract_address=_normalize_address(resolved_permit2),
            ),
        }
        self._payment_tokens = {
            "USDC": _normalize_address(resolved_usdc),
            "USDT": _normalize_address(resolved_usdt),
            "PWR": _normalize_address(resolved_pwr),
        }

    def contract(self, contract_name: str) -> ContractTarget:
        if contract_name not in self._targets:
            raise KeyError(contract_name)
        return self._targets[contract_name]

    def order_book(self) -> ContractTarget:
        return self.contract("OrderBook")

    def payment_router(self) -> ContractTarget:
        return self.contract("OrderPaymentRouter")

    def machine_asset(self) -> ContractTarget:
        return self.contract("MachineAssetNFT")

    def settlement_controller(self) -> ContractTarget:
        return self.contract("SettlementController")

    def machine_marketplace(self) -> ContractTarget:
        return self.contract("MachineMarketplace")

    def revenue_vault(self) -> ContractTarget:
        return self.contract("RevenueVault")

    def pwr_token(self) -> ContractTarget:
        return self.contract("PWRToken")

    def permit2(self) -> ContractTarget:
        return self.contract("Permit2")

    def payment_token(self, currency: str) -> str:
        normalized = currency.upper()
        if normalized not in self._payment_tokens:
            raise KeyError(normalized)
        return self._payment_tokens[normalized]


def _normalize_address(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _EVM_ADDRESS_RE.match(normalized):
        raise ValueError(f"invalid_evm_address:{value}")
    return normalized
