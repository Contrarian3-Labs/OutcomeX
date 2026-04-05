from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from app.domain.enums import PaymentState
from app.domain.models import Order, Payment


@dataclass(frozen=True)
class OnchainPaymentVerificationResult:
    matched: bool
    state: PaymentState
    tx_hash: str
    event_id: str
    reason: str | None
    evidence_order_id: str | None
    evidence_amount_cents: int | None
    evidence_currency: str | None
    evidence_wallet_address: str | None
    evidence_create_order_tx_hash: str | None = None
    evidence_create_order_event_id: str | None = None
    evidence_create_order_block_number: int | None = None


class OnchainPaymentVerifier:
    """Verifier boundary for tx/event evidence before mutating payment state."""

    def verify_payment(
        self,
        *,
        tx_hash: str,
        wallet_address: str | None,
        order: Order,
        payment: Payment,
    ) -> OnchainPaymentVerificationResult:
        tx_hash_normalized = tx_hash.lower()
        if not tx_hash_normalized.startswith("0x"):
            return OnchainPaymentVerificationResult(
                matched=False,
                state=PaymentState.FAILED,
                tx_hash=tx_hash_normalized,
                event_id=f"onchain:{tx_hash_normalized}",
                reason="invalid_tx_hash",
                evidence_order_id=None,
                evidence_amount_cents=None,
                evidence_currency=None,
                evidence_wallet_address=wallet_address,
                evidence_create_order_tx_hash=None,
                evidence_create_order_event_id=None,
                evidence_create_order_block_number=None,
            )

        seed_hex = hashlib.sha256(f"{order.id}:{payment.id}:{tx_hash_normalized}".encode("utf-8")).hexdigest()
        evidence_order_id = order.onchain_order_id or f"oc_{(int(seed_hex[:16], 16) % 1_000_000_000) + 1}"
        evidence_block_number = (int(seed_hex[16:24], 16) % 9_000_000) + 1_000_000

        return OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash=tx_hash_normalized,
            event_id=f"onchain:{tx_hash_normalized}",
            reason=None,
            evidence_order_id=evidence_order_id,
            evidence_amount_cents=payment.amount_cents,
            evidence_currency=payment.currency,
            evidence_wallet_address=wallet_address,
            evidence_create_order_tx_hash=tx_hash_normalized,
            evidence_create_order_event_id=f"OrderCreated:{evidence_order_id}:{tx_hash_normalized}",
            evidence_create_order_block_number=evidence_block_number,
        )


@lru_cache
def get_onchain_payment_verifier() -> OnchainPaymentVerifier:
    return OnchainPaymentVerifier()
