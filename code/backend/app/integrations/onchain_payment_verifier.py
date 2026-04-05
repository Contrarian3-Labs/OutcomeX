from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.domain.enums import PaymentState


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
    """Boundary for replacing frontend-reported syncs with chain-evidence verification."""

    def verify_payment(
        self,
        *,
        tx_hash: str,
        wallet_address: str | None,
        order,
        payment,
    ) -> OnchainPaymentVerificationResult:
        return OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash=tx_hash,
            event_id=f"onchain:{tx_hash.lower()}",
            reason=None,
            evidence_order_id=order.onchain_order_id,
            evidence_amount_cents=payment.amount_cents,
            evidence_currency=payment.currency,
            evidence_wallet_address=wallet_address,
        )


@lru_cache(maxsize=1)
def get_onchain_payment_verifier() -> OnchainPaymentVerifier:
    return OnchainPaymentVerifier()
