from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriteResult

CREATE_PAID_ORDER_SELECTOR = "0xcaf5331f"


class TransactionSender(Protocol):
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        ...


class NullTransactionSender:
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        return write_result


class JsonRpcClient:
    def __init__(self, *, rpc_url: str, timeout_seconds: float = 10.0) -> None:
        self._rpc_url = rpc_url
        self._timeout_seconds = timeout_seconds

    def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(self._rpc_url, json=payload)
            response.raise_for_status()
            body = response.json()
        if body.get("error") is not None:
            raise RuntimeError(f"json_rpc_error:{body['error']}")
        return body.get("result")


class PythonTransactionSender:
    """Pure-Python JSON-RPC sender for `createPaidOrderByAdapter`."""

    def __init__(
        self,
        *,
        rpc_url: str,
        private_key: str,
        contracts_registry: ContractsRegistry | None = None,
        rpc_client: JsonRpcClient | Any | None = None,
        account_factory=None,
    ) -> None:
        self._private_key = private_key
        self._contracts_registry = contracts_registry or ContractsRegistry()
        self._rpc_client = rpc_client or JsonRpcClient(rpc_url=rpc_url)
        self._account_factory = account_factory

    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        if write_result.method_name != "createPaidOrderByAdapter":
            return write_result

        account = self._build_account()
        sender_address = str(account.address).lower()
        target = self._contracts_registry.payment_router().contract_address
        data = self._encode_create_paid_call(write_result.payload)
        chain_id = int(self._rpc_client.call("eth_chainId", []), 16)
        nonce = int(self._rpc_client.call("eth_getTransactionCount", [sender_address, "pending"]), 16)
        gas_price = int(self._rpc_client.call("eth_gasPrice", []), 16)

        tx = {
            "to": target,
            "from": sender_address,
            "data": data,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": gas_price,
            "value": 0,
        }
        tx["gas"] = int(self._rpc_client.call("eth_estimateGas", [tx]), 16)

        signed = account.sign_transaction(tx)
        raw_tx = self._raw_transaction_hex(signed)
        tx_hash = str(self._rpc_client.call("eth_sendRawTransaction", [raw_tx])).lower()
        return replace(write_result, tx_hash=tx_hash)

    def _build_account(self):
        if self._account_factory is not None:
            return self._account_factory(self._private_key)
        try:
            from eth_account import Account
        except ModuleNotFoundError as exc:
            raise RuntimeError("eth-account is required for PythonTransactionSender") from exc
        return Account.from_key(self._private_key)

    @staticmethod
    def _raw_transaction_hex(signed: Any) -> str:
        raw_tx = getattr(signed, "raw_transaction", None)
        if raw_tx is None:
            raw_tx = getattr(signed, "rawTransaction", None)
        if raw_tx is None:
            raise RuntimeError("signed_transaction_missing_raw_bytes")
        return raw_tx.hex() if hasattr(raw_tx, "hex") else str(raw_tx)

    @staticmethod
    def _encode_create_paid_call(payload: dict[str, Any]) -> str:
        encoded = [
            PythonTransactionSender._encode_address(payload["buyer"]),
            PythonTransactionSender._encode_uint256(int(payload["machine_id"])),
            PythonTransactionSender._encode_uint256(int(payload["amount"])),
            PythonTransactionSender._encode_address(payload["payment_token_address"]),
        ]
        return CREATE_PAID_ORDER_SELECTOR + "".join(encoded)

    @staticmethod
    def _encode_uint256(value: int) -> str:
        return hex(value)[2:].rjust(64, "0")

    @staticmethod
    def _encode_address(value: str) -> str:
        normalized = value.lower().removeprefix("0x")
        return normalized.rjust(64, "0")


def get_onchain_transaction_sender() -> TransactionSender:
    settings = get_settings()
    if settings.onchain_rpc_url and settings.onchain_broadcaster_private_key:
        return PythonTransactionSender(
            rpc_url=settings.onchain_rpc_url,
            private_key=settings.onchain_broadcaster_private_key,
        )
    return NullTransactionSender()
