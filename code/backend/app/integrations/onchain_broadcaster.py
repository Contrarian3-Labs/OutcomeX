from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from app.domain.models import Order, Payment
from app.onchain.order_writer import OrderWriteResult


@dataclass(frozen=True)
class OnchainCreateOrderReceipt:
    onchain_order_id: str
    tx_hash: str
    event_id: str
    block_number: int


class OnchainBroadcaster:
    """Broadcast boundary for create-order style writes."""

    def broadcast_create_order(
        self,
        *,
        order: Order,
        write_result: OrderWriteResult | None,
    ) -> OnchainCreateOrderReceipt:
        seed_source = (
            f"create-only:{order.id}:{order.machine_id}:{order.quoted_amount_cents}:"
            f"{write_result.tx_hash if write_result is not None else 'no-write-result'}"
        )
        return self._build_receipt(
            seed_source=seed_source,
            fallback_tx_seed=f"create-order:{order.id}",
            event_prefix="OrderCreated",
            write_result=write_result,
        )

    def broadcast_create_order_and_mark_paid(
        self,
        *,
        order: Order,
        payment: Payment,
        write_result: OrderWriteResult | None,
    ) -> OnchainCreateOrderReceipt:
        seed_source = (
            f"create-paid:{order.id}:{order.machine_id}:{order.quoted_amount_cents}:{payment.id}:"
            f"{payment.amount_cents}:{payment.currency}:{write_result.tx_hash if write_result is not None else 'no-write-result'}"
        )
        return self._build_receipt(
            seed_source=seed_source,
            fallback_tx_seed=f"create-paid:{order.id}:{payment.id}",
            event_prefix="OrderCreated",
            write_result=write_result,
        )

    def broadcast_create_paid_order(
        self,
        *,
        order: Order,
        payment: Payment,
        write_result: OrderWriteResult | None,
    ) -> OnchainCreateOrderReceipt:
        return self.broadcast_create_order_and_mark_paid(
            order=order,
            payment=payment,
            write_result=write_result,
        )

    @staticmethod
    def _build_receipt(
        *,
        seed_source: str,
        fallback_tx_seed: str,
        event_prefix: str,
        write_result: OrderWriteResult | None,
    ) -> OnchainCreateOrderReceipt:
        seed_hex = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()
        tx_hash = (
            write_result.tx_hash
            if write_result is not None
            else "0x" + hashlib.sha256(fallback_tx_seed.encode("utf-8")).hexdigest()
        )
        order_id_numeric = (int(seed_hex[:16], 16) % 1_000_000_000) + 1
        block_number = (int(seed_hex[16:24], 16) % 9_000_000) + 1_000_000
        onchain_order_id = f"oc_{order_id_numeric}"
        event_id = f"{event_prefix}:{onchain_order_id}:{tx_hash.lower()}"
        return OnchainCreateOrderReceipt(
            onchain_order_id=onchain_order_id,
            tx_hash=tx_hash,
            event_id=event_id,
            block_number=block_number,
        )


@lru_cache
def get_onchain_broadcaster() -> OnchainBroadcaster:
    return OnchainBroadcaster()
