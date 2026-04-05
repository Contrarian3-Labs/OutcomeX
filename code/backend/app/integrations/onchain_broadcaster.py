from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from app.domain.models import Order
from app.onchain.order_writer import OrderWriteResult


@dataclass(frozen=True)
class OnchainCreateOrderReceipt:
    onchain_order_id: str
    tx_hash: str
    event_id: str
    block_number: int


class OnchainBroadcaster:
    """Broadcast boundary for createOrder writes."""

    def broadcast_create_order(
        self,
        *,
        order: Order,
        write_result: OrderWriteResult | None,
    ) -> OnchainCreateOrderReceipt:
        seed_source = (
            f"{order.id}:{order.machine_id}:{order.quoted_amount_cents}:"
            f"{write_result.tx_hash if write_result is not None else 'no-write-result'}"
        )
        seed_hex = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()
        tx_hash = (
            write_result.tx_hash
            if write_result is not None
            else "0x" + hashlib.sha256(f"create-order:{order.id}".encode("utf-8")).hexdigest()
        )

        order_id_numeric = (int(seed_hex[:16], 16) % 1_000_000_000) + 1
        block_number = (int(seed_hex[16:24], 16) % 9_000_000) + 1_000_000
        onchain_order_id = f"oc_{order_id_numeric}"
        event_id = f"OrderCreated:{onchain_order_id}:{tx_hash.lower()}"
        return OnchainCreateOrderReceipt(
            onchain_order_id=onchain_order_id,
            tx_hash=tx_hash,
            event_id=event_id,
            block_number=block_number,
        )


@lru_cache
def get_onchain_broadcaster() -> OnchainBroadcaster:
    return OnchainBroadcaster()
