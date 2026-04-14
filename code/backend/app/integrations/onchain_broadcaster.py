from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import time

from app.core.config import get_settings
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.event_decoder import decode_order_created_event
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

    def __init__(
        self,
        *,
        receipt_reader: ReceiptReader | None = None,
        require_live_receipt: bool = False,
        contracts_registry: ContractsRegistry | None = None,
        receipt_wait_seconds: float = 10.0,
    ) -> None:
        self._receipt_reader = receipt_reader or get_receipt_reader()
        self._require_live_receipt = require_live_receipt
        self._contracts_registry = contracts_registry or ContractsRegistry()
        self._receipt_wait_seconds = max(0.0, receipt_wait_seconds)

    def broadcast_create_order(self, *, write_result: OrderWriteResult) -> OnchainCreateOrderReceipt:
        return self._build_receipt(write_result=write_result, fallback_seed=write_result.idempotency_key)

    def broadcast_create_paid_order(self, *, write_result: OrderWriteResult) -> OnchainCreateOrderReceipt:
        return self._build_receipt(write_result=write_result, fallback_seed=f"paid:{write_result.idempotency_key}")

    def _build_receipt(self, *, write_result: OrderWriteResult, fallback_seed: str) -> OnchainCreateOrderReceipt:
        receipt = self._wait_for_receipt(write_result.tx_hash)
        if receipt is not None:
            decoded_event = None
            for contract_address in (
                self._contracts_registry.order_book().contract_address,
                write_result.contract_address,
            ):
                decoded_event = decode_order_created_event(
                    receipt=receipt,
                    contract_address=contract_address,
                )
                if decoded_event is not None:
                    break
            if decoded_event is not None:
                tx_hash = str(decoded_event["transaction_hash"]).lower()
                order_id = str(decoded_event["order_id"])
                return OnchainCreateOrderReceipt(
                    onchain_order_id=order_id,
                    tx_hash=tx_hash,
                    event_id=f"OrderCreated:{order_id}:{tx_hash}",
                    block_number=receipt.block_number,
                )
            onchain_order_id = self._derive_onchain_order_id(seed=f"{receipt.tx_hash}:{receipt.event_id}")
            return OnchainCreateOrderReceipt(
                onchain_order_id=onchain_order_id,
                tx_hash=receipt.tx_hash,
                event_id=receipt.event_id,
                block_number=receipt.block_number,
            )

        if self._require_live_receipt:
            raise RuntimeError(f"broadcast_receipt_missing:{write_result.tx_hash}")

        onchain_order_id = self._derive_onchain_order_id(seed=fallback_seed)
        return OnchainCreateOrderReceipt(
            onchain_order_id=onchain_order_id,
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:{onchain_order_id}:{write_result.tx_hash.lower()}",
            block_number=self._derive_block_number(seed=fallback_seed),
        )

    def _wait_for_receipt(self, tx_hash: str):
        if not self._require_live_receipt:
            return self._receipt_reader.get_receipt(tx_hash)

        deadline = time.time() + self._receipt_wait_seconds
        while time.time() < deadline:
            receipt = self._receipt_reader.get_receipt(tx_hash)
            if receipt is not None:
                return receipt
            time.sleep(0.25)
        return self._receipt_reader.get_receipt(tx_hash)

    @staticmethod
    def _derive_onchain_order_id(*, seed: str) -> str:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return str((int(digest[:16], 16) % 1_000_000_000) + 1)

    @staticmethod
    def _derive_block_number(*, seed: str) -> int:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return (int(digest[16:24], 16) % 9_000_000) + 1_000_000


@lru_cache
def get_onchain_broadcaster() -> OnchainBroadcaster:
    settings = get_settings()
    return OnchainBroadcaster(
        require_live_receipt=bool(settings.onchain_rpc_url.strip()),
        receipt_wait_seconds=settings.onchain_receipt_timeout_seconds,
    )
