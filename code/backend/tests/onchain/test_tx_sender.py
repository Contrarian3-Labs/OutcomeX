from datetime import datetime, timezone

import pytest

from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriteResult
from app.onchain.tx_sender import PythonTransactionSender


def _write_result() -> OrderWriteResult:
    return OrderWriteResult(
        tx_hash="0xsynthetic",
        submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
        chain_id=133,
        contract_name="OrderPaymentRouter",
        contract_address="0x0000000000000000000000000000000000000134",
        method_name="createPaidOrderByAdapter",
        idempotency_key="key",
        payload={
            "buyer": "0x2222222222222222222222222222222222222222",
            "machine_id": "7",
            "amount": 500,
            "payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        },
    )


class FakeRpcClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object]]] = []

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        responses = {
            "eth_chainId": "0x85",
            "eth_getTransactionCount": "0x3",
            "eth_gasPrice": "0x4a817c800",
            "eth_estimateGas": "0x5208",
            "eth_sendRawTransaction": "0xLiveHash",
        }
        return responses[method]


class FakeSignedTx:
    raw_transaction = bytes.fromhex("deadbeef")


class FakeAccount:
    def __init__(self) -> None:
        self.address = "0x9999999999999999999999999999999999999999"
        self.signed_txs: list[dict[str, object]] = []

    def sign_transaction(self, tx: dict[str, object]) -> FakeSignedTx:
        self.signed_txs.append(tx)
        return FakeSignedTx()


def test_python_sender_replaces_tx_hash_from_rpc_output() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        contracts_registry=ContractsRegistry(),
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    result = sender.send(_write_result())

    assert [method for method, _ in rpc_client.calls] == [
        "eth_chainId",
        "eth_getTransactionCount",
        "eth_gasPrice",
        "eth_estimateGas",
        "eth_sendRawTransaction",
    ]
    estimated_tx = rpc_client.calls[3][1][0]
    assert isinstance(estimated_tx, dict)
    assert estimated_tx["to"] == "0x0000000000000000000000000000000000000134"
    assert estimated_tx["from"] == "0x9999999999999999999999999999999999999999"
    assert str(estimated_tx["data"]).startswith("0xcaf5331f")
    assert account.signed_txs[0]["gas"] == 21000
    assert result.tx_hash == "0xlivehash"


def test_python_sender_leaves_non_hsp_methods_unchanged() -> None:
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        contracts_registry=ContractsRegistry(),
        rpc_client=FakeRpcClient(),
        account_factory=lambda private_key: pytest.fail("account factory should not be called"),
    )
    write_result = _write_result()
    write_result = OrderWriteResult(
        tx_hash=write_result.tx_hash,
        submitted_at=write_result.submitted_at,
        chain_id=write_result.chain_id,
        contract_name=write_result.contract_name,
        contract_address=write_result.contract_address,
        method_name="createOrderAndPayWithUSDC",
        idempotency_key=write_result.idempotency_key,
        payload=write_result.payload,
    )

    result = sender.send(write_result)

    assert result == write_result
