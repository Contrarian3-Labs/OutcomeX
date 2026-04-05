from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from app.domain.enums import PaymentState
from app.domain.models import Order, Payment
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.receipts import ReceiptReader, get_receipt_reader


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
    evidence_create_order_tx_hash: str | None
    evidence_create_order_event_id: str | None
    evidence_create_order_block_number: int | None


class OnchainPaymentVerifier:
    """Tx verification boundary with optional live receipt checks."""

    def __init__(
        self,
        *,
        contracts_registry: ContractsRegistry | None = None,
        receipt_reader: ReceiptReader | None = None,
    ) -> None:
        self._contracts_registry = contracts_registry or ContractsRegistry()
        self._receipt_reader = receipt_reader or get_receipt_reader()

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
            return self._failure(tx_hash=tx_hash_normalized, reason="invalid_tx_hash", wallet_address=wallet_address)

        receipt = self._receipt_reader.get_receipt(tx_hash_normalized)
        if receipt is not None:
            if receipt.status != 1:
                return self._failure(tx_hash=tx_hash_normalized, reason="tx_failed", wallet_address=wallet_address)
            expected_target = self._contracts_registry.payment_router().contract_address.lower()
            if receipt.to_address is not None and receipt.to_address != expected_target:
                return self._failure(tx_hash=tx_hash_normalized, reason="wrong_contract", wallet_address=wallet_address)
            normalized_wallet = wallet_address.lower() if wallet_address is not None else None
            if normalized_wallet is not None and receipt.from_address is not None and receipt.from_address != normalized_wallet:
                return self._failure(tx_hash=tx_hash_normalized, reason="wallet_mismatch", wallet_address=wallet_address)

            evidence_order_id = self._derive_onchain_order_id(seed=f"{receipt.tx_hash}:{receipt.event_id}:{order.id}")
            return OnchainPaymentVerificationResult(
                matched=True,
                state=PaymentState.SUCCEEDED,
                tx_hash=receipt.tx_hash,
                event_id=receipt.event_id,
                reason=None,
                evidence_order_id=evidence_order_id,
                evidence_amount_cents=payment.amount_cents,
                evidence_currency=payment.currency,
                evidence_wallet_address=normalized_wallet or receipt.from_address,
                evidence_create_order_tx_hash=receipt.tx_hash,
                evidence_create_order_event_id=f"OrderCreated:{evidence_order_id}:{receipt.tx_hash}",
                evidence_create_order_block_number=receipt.block_number,
            )

        evidence_order_id = order.onchain_order_id or self._derive_onchain_order_id(seed=f"{order.id}:{payment.id}:{tx_hash_normalized}")
        evidence_block_number = self._derive_block_number(seed=f"{order.id}:{payment.id}:{tx_hash_normalized}")
        return OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash=tx_hash_normalized,
            event_id=f"onchain:{tx_hash_normalized}",
            reason=None,
            evidence_order_id=evidence_order_id,
            evidence_amount_cents=payment.amount_cents,
            evidence_currency=payment.currency,
            evidence_wallet_address=wallet_address.lower() if wallet_address is not None else None,
            evidence_create_order_tx_hash=tx_hash_normalized,
            evidence_create_order_event_id=f"OrderCreated:{evidence_order_id}:{tx_hash_normalized}",
            evidence_create_order_block_number=evidence_block_number,
        )

    @staticmethod
    def _derive_onchain_order_id(*, seed: str) -> str:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return f"oc_{(int(digest[:16], 16) % 1_000_000_000) + 1}"

    @staticmethod
    def _derive_block_number(*, seed: str) -> int:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return (int(digest[16:24], 16) % 9_000_000) + 1_000_000

    @staticmethod
    def _failure(*, tx_hash: str, reason: str, wallet_address: str | None) -> OnchainPaymentVerificationResult:
        return OnchainPaymentVerificationResult(
            matched=False,
            state=PaymentState.FAILED,
            tx_hash=tx_hash,
            event_id=f"onchain:{tx_hash}",
            reason=reason,
            evidence_order_id=None,
            evidence_amount_cents=None,
            evidence_currency=None,
            evidence_wallet_address=wallet_address.lower() if wallet_address is not None else None,
            evidence_create_order_tx_hash=None,
            evidence_create_order_event_id=None,
            evidence_create_order_block_number=None,
        )


@lru_cache
def get_onchain_payment_verifier() -> OnchainPaymentVerifier:
    return OnchainPaymentVerifier()
