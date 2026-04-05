from __future__ import annotations

from dataclasses import replace
import json
import subprocess
from typing import Protocol

from app.core.config import get_settings
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriteResult

CREATE_PAID_ORDER_SIGNATURE = "createPaidOrderByAdapter(address,uint256,uint256,address)"


class TransactionSender(Protocol):
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        ...


class NullTransactionSender:
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        return write_result


class CastTransactionSender:
    """Use `cast send` as a pragmatic live-broadcast boundary."""

    def __init__(
        self,
        *,
        rpc_url: str,
        private_key: str,
        contracts_registry: ContractsRegistry | None = None,
        runner=None,
    ) -> None:
        self._rpc_url = rpc_url
        self._private_key = private_key
        self._contracts_registry = contracts_registry or ContractsRegistry()
        self._runner = runner or subprocess.run

    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        if write_result.method_name != "createPaidOrderByAdapter":
            return write_result

        payload = write_result.payload
        command = [
            "cast",
            "send",
            self._contracts_registry.payment_router().contract_address,
            CREATE_PAID_ORDER_SIGNATURE,
            payload["buyer"],
            str(payload["machine_id"]),
            str(payload["amount"]),
            payload["payment_token_address"],
            "--rpc-url",
            self._rpc_url,
            "--private-key",
            self._private_key,
            "--json",
        ]
        completed = self._runner(command, capture_output=True, text=True, check=True)
        parsed = json.loads(completed.stdout or "{}")
        tx_hash = str(
            parsed.get("transactionHash")
            or parsed.get("hash")
            or parsed.get("txHash")
            or write_result.tx_hash
        ).lower()
        return replace(write_result, tx_hash=tx_hash)


def get_onchain_transaction_sender() -> TransactionSender:
    settings = get_settings()
    if settings.onchain_rpc_url and settings.onchain_broadcaster_private_key:
        return CastTransactionSender(
            rpc_url=settings.onchain_rpc_url,
            private_key=settings.onchain_broadcaster_private_key,
        )
    return NullTransactionSender()
