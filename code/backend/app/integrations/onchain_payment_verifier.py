from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

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
            )

        # Stub adapter: until real chain integration is wired, this boundary returns
        # normalized evidence anchored to persisted payment/order records.
        return OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash=tx_hash_normalized,
            event_id=f"onchain:{tx_hash_normalized}",
            reason=None,
            evidence_order_id=order.onchain_order_id or order.id,
            evidence_amount_cents=payment.amount_cents,
            evidence_currency=payment.currency,
            evidence_wallet_address=wallet_address,
        )


@lru_cache
def get_onchain_payment_verifier() -> OnchainPaymentVerifier:
    return OnchainPaymentVerifier()
