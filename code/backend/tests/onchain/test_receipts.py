import pytest

from app.core.config import reset_settings_cache
from app.onchain.receipts import JsonRpcReceiptReader, NullReceiptReader, get_receipt_reader


@pytest.fixture(autouse=True)
def _reset_settings_cache_between_tests():
    reset_settings_cache()
    get_receipt_reader.cache_clear()
    yield
    reset_settings_cache()
    get_receipt_reader.cache_clear()


def test_get_receipt_reader_returns_null_without_rpc(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")

    reader = get_receipt_reader()

    assert isinstance(reader, NullReceiptReader)


def test_get_receipt_reader_uses_configured_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "https://rpc.local")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RECEIPT_TIMEOUT_SECONDS", "27.5")

    reader = get_receipt_reader()

    assert isinstance(reader, JsonRpcReceiptReader)
    assert reader._timeout_seconds == 27.5
