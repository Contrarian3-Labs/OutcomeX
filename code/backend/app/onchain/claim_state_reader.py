from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.tx_sender import JsonRpcClient, PythonTransactionSender

REFUNDABLE_BY_TOKEN_SELECTOR = "0xed7b5281"
PLATFORM_ACCRUED_BY_TOKEN_SELECTOR = "0x549b1179"
CLAIMABLE_BY_MACHINE_OWNER_SELECTOR = "0xc0bd4ed7"


class SettlementClaimStateReader(Protocol):
    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        ...

    def platform_accrued_amount(self, *, currency: str) -> int:
        ...

    def machine_claimable_amount(self, *, onchain_machine_id: str, owner_user_id: str) -> int:
        ...


class NullSettlementClaimStateReader:
    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        return 0

    def platform_accrued_amount(self, *, currency: str) -> int:
        return 0

    def machine_claimable_amount(self, *, onchain_machine_id: str, owner_user_id: str) -> int:
        return 0


class JsonRpcSettlementClaimStateReader:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        contracts_registry: ContractsRegistry | None = None,
        buyer_address_resolver: BuyerAddressResolver | None = None,
        rpc_client: JsonRpcClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._contracts_registry = contracts_registry or ContractsRegistry(settings=self._settings)
        self._buyer_address_resolver = buyer_address_resolver or BuyerAddressResolver.from_json(
            self._settings.buyer_wallet_map_json
        )
        self._rpc_client = rpc_client or JsonRpcClient(
            rpc_url=self._settings.onchain_rpc_url,
            timeout_seconds=self._settings.onchain_receipt_timeout_seconds,
        )

    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        buyer_wallet = self._buyer_address_resolver.resolve_wallet(user_id)
        if buyer_wallet is None:
            raise RuntimeError("buyer_wallet_unresolved")
        data = (
            REFUNDABLE_BY_TOKEN_SELECTOR
            + PythonTransactionSender._encode_address(buyer_wallet)
            + PythonTransactionSender._encode_address(self._contracts_registry.payment_token(currency))
        )
        return self._call_uint256(
            contract_address=self._contracts_registry.settlement_controller().contract_address,
            data=data,
        )

    def platform_accrued_amount(self, *, currency: str) -> int:
        data = PLATFORM_ACCRUED_BY_TOKEN_SELECTOR + PythonTransactionSender._encode_address(
            self._contracts_registry.payment_token(currency)
        )
        return self._call_uint256(
            contract_address=self._contracts_registry.settlement_controller().contract_address,
            data=data,
        )

    def machine_claimable_amount(self, *, onchain_machine_id: str, owner_user_id: str) -> int:
        owner_wallet = self._buyer_address_resolver.resolve_wallet(owner_user_id)
        if owner_wallet is None:
            raise RuntimeError("machine_owner_wallet_unresolved")
        try:
            machine_id = int(str(onchain_machine_id))
        except ValueError as exc:
            raise RuntimeError("invalid_onchain_machine_id") from exc
        data = (
            CLAIMABLE_BY_MACHINE_OWNER_SELECTOR
            + PythonTransactionSender._encode_uint256(machine_id)
            + PythonTransactionSender._encode_address(owner_wallet)
        )
        return self._call_uint256(
            contract_address=self._contracts_registry.revenue_vault().contract_address,
            data=data,
        )

    def _call_uint256(self, *, contract_address: str, data: str) -> int:
        result = self._rpc_client.call(
            "eth_call",
            [
                {
                    "to": contract_address,
                    "data": data,
                },
                "latest",
            ],
        )
        if not result:
            return 0
        return int(str(result), 16)


@lru_cache
def get_settlement_claim_state_reader() -> SettlementClaimStateReader:
    settings = get_settings()
    if not settings.onchain_rpc_url.strip():
        return NullSettlementClaimStateReader()
    return JsonRpcSettlementClaimStateReader(settings=settings)
