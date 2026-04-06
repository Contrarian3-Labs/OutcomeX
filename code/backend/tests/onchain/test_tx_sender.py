from datetime import datetime, timezone

import pytest

from app.core.config import reset_settings_cache
from app.onchain.order_writer import OrderWriteResult
from app.onchain.tx_sender import (
    CLAIM_MACHINE_REVENUE_SELECTOR,
    CLAIM_PLATFORM_REVENUE_SELECTOR,
    CLAIM_REFUND_SELECTOR,
    CONFIRM_RESULT_SELECTOR,
    CREATE_PAID_ORDER_SELECTOR,
    MARK_PREVIEW_READY_SELECTOR,
    MINT_MACHINE_SELECTOR,
    NullTransactionSender,
    PythonTransactionSender,
    get_onchain_transaction_sender,
)


def _write_result(
    *,
    method_name: str = "createPaidOrderByAdapter",
    contract_name: str = "OrderPaymentRouter",
    contract_address: str = "0x0000000000000000000000000000000000000134",
    payload: dict | None = None,
) -> OrderWriteResult:
    return OrderWriteResult(
        tx_hash="0xsynthetic",
        submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
        chain_id=133,
        contract_name=contract_name,
        contract_address=contract_address,
        method_name=method_name,
        idempotency_key="key",
        payload=payload
        or {
            "buyer": "0x2222222222222222222222222222222222222222",
            "machine_id": "7",
            "amount": 500,
            "payment_token_address": "0x79AEc4eea31d50792f61d1ca0733c18c89524c9e",
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


@pytest.fixture(autouse=True)
def _reset_settings_cache_between_tests():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_python_sender_replaces_tx_hash_for_create_paid_order() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
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
    assert str(estimated_tx["data"]).startswith(CREATE_PAID_ORDER_SELECTOR)
    assert account.signed_txs[0]["gas"] == 21000
    assert result.tx_hash == "0xlivehash"


def test_python_sender_encodes_mark_preview_ready_and_uses_method_key() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        method_private_keys={"markPreviewReady": "0xdef"},
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    result = sender.send(
        _write_result(
            method_name="markPreviewReady",
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            payload={
                "order_id": "42",
                "preview_state": "ready",
                "execution_state": "succeeded",
            },
        )
    )

    estimated_tx = rpc_client.calls[3][1][0]
    assert str(estimated_tx["data"]).startswith(MARK_PREVIEW_READY_SELECTOR)
    assert estimated_tx["to"] == "0x0000000000000000000000000000000000000133"
    assert result.tx_hash == "0xlivehash"


def test_python_sender_encodes_mint_machine_with_dynamic_uri() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    sender.send(
        _write_result(
            method_name="mintMachine",
            contract_name="MachineAssetNFT",
            contract_address="0x0000000000000000000000000000000000000132",
            payload={"to": "0x2222222222222222222222222222222222222222", "uri": "ipfs://machine-001"},
        )
    )

    estimated_tx = rpc_client.calls[3][1][0]
    assert str(estimated_tx["data"]).startswith(MINT_MACHINE_SELECTOR)
    assert estimated_tx["to"] == "0x0000000000000000000000000000000000000132"


def test_python_sender_encodes_confirm_result_with_buyer_key() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        method_private_keys={"confirmResult": "0xfeed"},
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    sender.send(
        _write_result(
            method_name="confirmResult",
            contract_name="OrderBook",
            contract_address="0x0000000000000000000000000000000000000133",
            payload={"order_id": "0x2a"},
        )
    )

    estimated_tx = rpc_client.calls[3][1][0]
    assert str(estimated_tx["data"]).startswith(CONFIRM_RESULT_SELECTOR)
    assert estimated_tx["to"] == "0x0000000000000000000000000000000000000133"


def test_python_sender_encodes_claim_machine_revenue() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        method_private_keys={"claimMachineRevenue": "0xfeed"},
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    sender.send(
        _write_result(
            method_name="claimMachineRevenue",
            contract_name="RevenueVault",
            contract_address="0x0000000000000000000000000000000000000136",
            payload={"machine_id": "5"},
        )
    )

    estimated_tx = rpc_client.calls[3][1][0]
    assert str(estimated_tx["data"]).startswith(CLAIM_MACHINE_REVENUE_SELECTOR)
    assert estimated_tx["to"] == "0x0000000000000000000000000000000000000136"


def test_python_sender_encodes_claim_refund_and_platform_revenue() -> None:
    rpc_client = FakeRpcClient()
    account = FakeAccount()
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        method_private_keys={
            "claimRefund": "0xfeed",
            "claimPlatformRevenue": "0xbeef",
        },
        rpc_client=rpc_client,
        account_factory=lambda private_key: account,
    )

    sender.send(
        _write_result(
            method_name="claimRefund",
            contract_name="SettlementController",
            contract_address="0x0000000000000000000000000000000000000135",
            payload={"payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"},
        )
    )
    sender.send(
        _write_result(
            method_name="claimPlatformRevenue",
            contract_name="SettlementController",
            contract_address="0x0000000000000000000000000000000000000135",
            payload={"payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"},
        )
    )

    first_estimated_tx = rpc_client.calls[3][1][0]
    second_estimated_tx = rpc_client.calls[8][1][0]
    assert str(first_estimated_tx["data"]).startswith(CLAIM_REFUND_SELECTOR)
    assert str(second_estimated_tx["data"]).startswith(CLAIM_PLATFORM_REVENUE_SELECTOR)
    assert first_estimated_tx["to"] == "0x0000000000000000000000000000000000000135"
    assert second_estimated_tx["to"] == "0x0000000000000000000000000000000000000135"


def test_python_sender_skips_supported_method_when_private_key_missing() -> None:
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        rpc_client=FakeRpcClient(),
        account_factory=lambda private_key: pytest.fail("account factory should not be called"),
    )
    write_result = _write_result(
        method_name="confirmResult",
        contract_name="OrderBook",
        contract_address="0x0000000000000000000000000000000000000133",
        payload={"order_id": "42"},
    )

    result = sender.send(write_result)

    assert result == write_result


def test_python_sender_leaves_unsupported_methods_unchanged() -> None:
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        rpc_client=FakeRpcClient(),
        account_factory=lambda private_key: pytest.fail("account factory should not be called"),
    )
    write_result = _write_result(
        method_name="createOrderAndPayWithUSDC",
        payload={"machine_id": "1", "amount": 100},
    )

    result = sender.send(write_result)

    assert result == write_result


def test_python_sender_raises_on_chain_id_mismatch() -> None:
    sender = PythonTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        rpc_client=FakeRpcClient(),
        account_factory=lambda private_key: FakeAccount(),
    )
    write_result = _write_result(
        method_name="confirmResult",
        contract_name="OrderBook",
        contract_address="0x0000000000000000000000000000000000000133",
        payload={"order_id": "42"},
    )
    write_result = OrderWriteResult(
        tx_hash=write_result.tx_hash,
        submitted_at=write_result.submitted_at,
        chain_id=31337,
        contract_name=write_result.contract_name,
        contract_address=write_result.contract_address,
        method_name=write_result.method_name,
        idempotency_key=write_result.idempotency_key,
        payload=write_result.payload,
    )

    with pytest.raises(RuntimeError, match="chain_id_mismatch"):
        sender.send(write_result)


def test_get_onchain_transaction_sender_returns_null_without_rpc(monkeypatch) -> None:
    monkeypatch.delenv("OUTCOMEX_ONCHAIN_RPC_URL", raising=False)
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ADAPTER_PRIVATE_KEY", "0xabc")

    sender = get_onchain_transaction_sender()

    assert isinstance(sender, NullTransactionSender)


def test_get_onchain_transaction_sender_returns_python_sender_with_rpc_and_signer(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "https://rpc.local")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ADAPTER_PRIVATE_KEY", "0xabc")

    sender = get_onchain_transaction_sender()

    assert isinstance(sender, PythonTransactionSender)
