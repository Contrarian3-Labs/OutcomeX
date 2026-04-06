from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.tx_sender import JsonRpcClient, PythonTransactionSender

REFUNDABLE_BY_TOKEN_SELECTOR = "0xed7b5281"
PLATFORM_ACCRUED_BY_TOKEN_SELECTOR = "0x549b1179"


class SettlementClaimStateReader(Protocol):
    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        ...

    def platform_accrued_amount(self, *, currency: str) -> int:
        ...


class NullSettlementClaimStateReader:
    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        return 0

    def platform_accrued_amount(self, *, currency: str) -> int:
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
