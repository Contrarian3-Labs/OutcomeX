from datetime import datetime, timezone

import pytest

from app.core.config import Settings
from app.onchain.lifecycle_service import OnchainLifecycleService
from app.onchain.order_writer import OrderWriteResult
from app.onchain.receipts import ChainReceipt


def _write_result() -> OrderWriteResult:
    return OrderWriteResult(
        tx_hash="0xsynthetic",
        submitted_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
        chain_id=133,
        contract_name="SettlementController",
        contract_address="0x0000000000000000000000000000000000000135",
        method_name="claimPlatformRevenue",
        idempotency_key="key",
        payload={"payment_token_address": "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"},
    )


class FakeSender:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        return write_result


class StubReceiptReader:
    def __init__(self, receipt: ChainReceipt | None) -> None:
        self.receipt = receipt

    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        return self.receipt


class FlakyReceiptReader:
    def __init__(self, *responses) -> None:
        self._responses = list(responses)

    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_send_as_treasury_raises_when_receipt_missing(monkeypatch) -> None:
    monkeypatch.setattr("app.onchain.lifecycle_service.PythonTransactionSender", FakeSender)
    service = OnchainLifecycleService(
        settings=Settings(
            onchain_rpc_url="http://127.0.0.1:8545",
            onchain_platform_treasury_private_key="0xabc",
            onchain_tx_timeout_seconds=0.1,
        )
    )
    service._receipt_reader = StubReceiptReader(None)

    with pytest.raises(RuntimeError, match="transaction_receipt_missing:0xsynthetic"):
        service.send_as_treasury(write_result=_write_result())


def test_send_as_treasury_raises_when_receipt_status_is_failed(monkeypatch) -> None:
    monkeypatch.setattr("app.onchain.lifecycle_service.PythonTransactionSender", FakeSender)
    service = OnchainLifecycleService(
        settings=Settings(
            onchain_rpc_url="http://127.0.0.1:8545",
            onchain_platform_treasury_private_key="0xabc",
            onchain_tx_timeout_seconds=0.1,
        )
    )
    service._receipt_reader = StubReceiptReader(
        ChainReceipt(
            tx_hash="0xsynthetic",
            status=0,
            from_address="0x9999999999999999999999999999999999999999",
            to_address="0x0000000000000000000000000000000000000135",
            block_number=123,
            event_id="receipt:0xsynthetic:123",
        )
    )

    with pytest.raises(RuntimeError, match="transaction_failed:0xsynthetic"):
        service.send_as_treasury(write_result=_write_result())


def test_send_as_treasury_retries_after_transient_receipt_error(monkeypatch) -> None:
    monkeypatch.setattr("app.onchain.lifecycle_service.PythonTransactionSender", FakeSender)
    service = OnchainLifecycleService(
        settings=Settings(
            onchain_rpc_url="http://127.0.0.1:8545",
            onchain_platform_treasury_private_key="0xabc",
            onchain_tx_timeout_seconds=0.5,
        )
    )
    service._receipt_reader = FlakyReceiptReader(
        RuntimeError("temporary_rpc_reset"),
        ChainReceipt(
            tx_hash="0xsynthetic",
            status=1,
            from_address="0x9999999999999999999999999999999999999999",
            to_address="0x0000000000000000000000000000000000000135",
            block_number=123,
            event_id="receipt:0xsynthetic:123",
        ),
    )

    receipt = service.send_as_treasury(write_result=_write_result())

    assert receipt.tx_hash == "0xsynthetic"
    assert receipt.receipt is not None
    assert receipt.receipt.status == 1


class _CapturingSender(FakeSender):
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        self.last_write_result = write_result
        return write_result


def test_send_as_user_prefers_expected_wallet_signer_from_explicit_keys(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def sender_factory(**kwargs):
        captured.update(kwargs)
        return FakeSender(**kwargs)

    monkeypatch.setattr("app.onchain.lifecycle_service.PythonTransactionSender", sender_factory)
    service = OnchainLifecycleService(
        settings=Settings(
            onchain_rpc_url="http://127.0.0.1:8545",
            buyer_wallet_map_json='{"owner-1":"0x70997970c51812dc3a010c7d01b50e0d17dc79c8"}',
            user_signer_private_keys_json='{"owner-1":"0x59c6995e998f97a5a0044976f8a35f1dcd80e45f5c7b0f0f3a6cce8d7b5a5a6d"}',
            onchain_machine_owner_private_key="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
            onchain_tx_timeout_seconds=0.1,
        )
    )
    service._receipt_reader = StubReceiptReader(
        ChainReceipt(
            tx_hash="0xsynthetic",
            status=1,
            from_address="0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            to_address="0x0000000000000000000000000000000000000135",
            block_number=123,
            event_id="receipt:0xsynthetic:123",
        )
    )

    service.send_as_user(user_id="owner-1", write_result=_write_result())

    assert captured["private_key"] == "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
