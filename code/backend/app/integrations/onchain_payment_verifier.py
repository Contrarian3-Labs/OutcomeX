from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.domain.enums import PaymentState
from app.domain.models import Order, Payment
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.event_decoder import decode_order_created_event, decode_payment_finalized_event
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

            normalized_wallet = wallet_address.lower() if wallet_address is not None else None
            expected_chain_amount = self._expected_chain_amount(payment)
            payment_event = decode_payment_finalized_event(
                receipt=receipt,
                contract_address=self._contracts_registry.payment_router().contract_address,
            )
            if payment_event is None:
                return self._failure(
                    tx_hash=tx_hash_normalized,
                    reason="payment_received_event_not_found",
                    wallet_address=wallet_address,
                )
            evidence_order_id = str(payment_event["order_id"])
            if order.onchain_order_id is not None and evidence_order_id != str(order.onchain_order_id):
                return self._failure(tx_hash=tx_hash_normalized, reason="payment_order_id_mismatch", wallet_address=wallet_address)
            if order.onchain_machine_id and str(payment_event.get("machine_id")) != str(order.onchain_machine_id):
                return self._failure(tx_hash=tx_hash_normalized, reason="machine_id_mismatch", wallet_address=wallet_address)
            expected_token = self._contracts_registry.payment_token(payment.currency.upper())
            if str(payment_event["token"]) != expected_token:
                return self._failure(tx_hash=tx_hash_normalized, reason="payment_token_mismatch", wallet_address=wallet_address)
            if payment_event.get("amount") != expected_chain_amount:
                return self._failure(tx_hash=tx_hash_normalized, reason="payment_amount_mismatch", wallet_address=wallet_address)
            if normalized_wallet is not None and str(payment_event.get("buyer")) != normalized_wallet:
                return self._failure(tx_hash=tx_hash_normalized, reason="buyer_mismatch", wallet_address=wallet_address)
            if normalized_wallet is not None and str(payment_event["payer"]) != normalized_wallet:
                return self._failure(tx_hash=tx_hash_normalized, reason="payer_mismatch", wallet_address=wallet_address)

            decoded_event = decode_order_created_event(
                receipt=receipt,
                contract_address=self._contracts_registry.order_book().contract_address,
            )
            if order.onchain_order_id is None and decoded_event is None:
                return self._failure(
                    tx_hash=tx_hash_normalized,
                    reason="order_created_event_not_found",
                    wallet_address=wallet_address,
                )
            if decoded_event is not None:
                if str(decoded_event["order_id"]) != evidence_order_id:
                    return self._failure(
                        tx_hash=tx_hash_normalized,
                        reason="order_created_payment_order_id_mismatch",
                        wallet_address=wallet_address,
                    )
                if order.onchain_machine_id and str(decoded_event.get("machine_id")) != str(order.onchain_machine_id):
                    return self._failure(tx_hash=tx_hash_normalized, reason="machine_id_mismatch", wallet_address=wallet_address)
                if decoded_event.get("gross_amount") != expected_chain_amount:
                    return self._failure(tx_hash=tx_hash_normalized, reason="amount_mismatch", wallet_address=wallet_address)
                if normalized_wallet is not None and decoded_event.get("buyer") != normalized_wallet:
                    return self._failure(tx_hash=tx_hash_normalized, reason="buyer_mismatch", wallet_address=wallet_address)

            evidence_create_order_tx_hash = receipt.tx_hash if decoded_event is not None else None
            evidence_create_order_event_id = (
                f"OrderCreated:{evidence_order_id}:{receipt.tx_hash}" if decoded_event is not None else None
            )
            evidence_create_order_block_number = receipt.block_number if decoded_event is not None else None
            evidence_event_id = evidence_create_order_event_id or f"PaymentFinalized:{evidence_order_id}:{receipt.tx_hash}"
            return OnchainPaymentVerificationResult(
                matched=True,
                state=PaymentState.SUCCEEDED,
                tx_hash=receipt.tx_hash,
                event_id=evidence_event_id,
                reason=None,
                evidence_order_id=evidence_order_id,
                evidence_amount_cents=payment.amount_cents,
                evidence_currency=payment.currency,
                evidence_wallet_address=normalized_wallet or receipt.from_address,
                evidence_create_order_tx_hash=evidence_create_order_tx_hash,
                evidence_create_order_event_id=evidence_create_order_event_id,
                evidence_create_order_block_number=evidence_create_order_block_number,
            )

        return self._failure(tx_hash=tx_hash_normalized, reason="receipt_not_found", wallet_address=wallet_address)

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

    @staticmethod
    def _expected_chain_amount(payment: Payment) -> int:
        if payment.currency.upper() != "PWR":
            return int(payment.amount_cents)
        provider_payload = dict(payment.provider_payload or {})
        direct_intent_payload = dict(provider_payload.get("direct_intent_payload") or {})
        pwr_amount = direct_intent_payload.get("pwr_amount")
        if pwr_amount is None:
            return int(payment.amount_cents)
        return int(str(pwr_amount))


@lru_cache
def get_onchain_payment_verifier() -> OnchainPaymentVerifier:
    return OnchainPaymentVerifier()
