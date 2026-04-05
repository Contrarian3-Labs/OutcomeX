from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from app.onchain.order_writer import OrderWriteResult
from app.onchain.receipts import ReceiptReader, get_receipt_reader


@dataclass(frozen=True)
class OnchainCreateOrderReceipt:
    onchain_order_id: str
    tx_hash: str
    event_id: str
    block_number: int


class OnchainBroadcaster:
    """Create-order receipt boundary with optional live receipt awareness."""

    def __init__(self, *, receipt_reader: ReceiptReader | None = None) -> None:
        self._receipt_reader = receipt_reader or get_receipt_reader()

    def broadcast_create_order(self, *, write_result: OrderWriteResult) -> OnchainCreateOrderReceipt:
        return self._build_receipt(write_result=write_result, fallback_seed=write_result.idempotency_key)

    def broadcast_create_paid_order(self, *, write_result: OrderWriteResult) -> OnchainCreateOrderReceipt:
        return self._build_receipt(write_result=write_result, fallback_seed=f"paid:{write_result.idempotency_key}")

    def _build_receipt(self, *, write_result: OrderWriteResult, fallback_seed: str) -> OnchainCreateOrderReceipt:
        receipt = self._receipt_reader.get_receipt(write_result.tx_hash)
        if receipt is not None:
            onchain_order_id = self._derive_onchain_order_id(seed=f"{receipt.tx_hash}:{receipt.event_id}")
            return OnchainCreateOrderReceipt(
                onchain_order_id=onchain_order_id,
                tx_hash=receipt.tx_hash,
                event_id=receipt.event_id,
                block_number=receipt.block_number,
            )

        onchain_order_id = self._derive_onchain_order_id(seed=fallback_seed)
        return OnchainCreateOrderReceipt(
            onchain_order_id=onchain_order_id,
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:{onchain_order_id}:{write_result.tx_hash.lower()}",
            block_number=self._derive_block_number(seed=fallback_seed),
        )

    @staticmethod
    def _derive_onchain_order_id(*, seed: str) -> str:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return f"oc_{(int(digest[:16], 16) % 1_000_000_000) + 1}"

    @staticmethod
    def _derive_block_number(*, seed: str) -> int:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return (int(digest[16:24], 16) % 9_000_000) + 1_000_000


@lru_cache
def get_onchain_broadcaster() -> OnchainBroadcaster:
    return OnchainBroadcaster()
