from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from typing import Any

from app.domain.models import Order, Payment, SettlementRecord
from app.onchain.contracts_registry import ContractsRegistry


@dataclass(frozen=True)
class OrderWriteResult:
    tx_hash: str
    submitted_at: datetime
    chain_id: int
    contract_name: str
    method_name: str
    idempotency_key: str
    payload: dict[str, Any]


class OrderWriter:
    def __init__(self, contracts_registry: ContractsRegistry | None = None) -> None:
        self._contracts_registry = contracts_registry or ContractsRegistry()

    def create_order(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": order.id,
            "machine_id": order.machine_id,
            "quoted_amount_cents": order.quoted_amount_cents,
            "user_id": order.user_id,
        }
        return self._submit("createOrder", payload)

    def mark_order_paid(self, order: Order, payment: Payment) -> OrderWriteResult:
        payload = {
            "order_id": order.id,
            "payment_id": payment.id,
            "merchant_order_id": payment.merchant_order_id,
            "flow_id": payment.flow_id,
            "provider_reference": payment.provider_reference,
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
            "settlement_beneficiary_user_id": order.settlement_beneficiary_user_id,
            "settlement_is_self_use": order.settlement_is_self_use,
            "settlement_is_dividend_eligible": order.settlement_is_dividend_eligible,
        }
        return self._submit("markOrderPaid", payload)

    def mark_preview_ready(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": order.id,
            "preview_state": order.preview_state.value,
            "execution_state": order.execution_state.value,
        }
        return self._submit("markPreviewReady", payload)

    def confirm_result(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": order.id,
            "result_confirmed_at": order.result_confirmed_at.isoformat() if order.result_confirmed_at else None,
            "settlement_state": order.settlement_state.value,
        }
        return self._submit("confirmResult", payload)

    def settle_order(self, order: Order, settlement: SettlementRecord) -> OrderWriteResult:
        payload = {
            "order_id": order.id,
            "settlement_id": settlement.id,
            "gross_amount_cents": settlement.gross_amount_cents,
            "platform_fee_cents": settlement.platform_fee_cents,
            "machine_share_cents": settlement.machine_share_cents,
            "settlement_beneficiary_user_id": order.settlement_beneficiary_user_id,
            "settlement_is_dividend_eligible": order.settlement_is_dividend_eligible,
        }
        return self._submit("settleOrder", payload)

    def _submit(self, method_name: str, payload: dict[str, Any]) -> OrderWriteResult:
        target = self._contracts_registry.order_book()
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=self._json_default)
        idempotency_key = hashlib.sha256(f"{method_name}:{canonical_payload}".encode("utf-8")).hexdigest()
        tx_hash = "0x" + hashlib.sha256(
            f"{target.contract_name}:{target.contract_address}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return OrderWriteResult(
            tx_hash=tx_hash,
            submitted_at=datetime.now(timezone.utc),
            chain_id=target.chain_id,
            contract_name=target.contract_name,
            method_name=method_name,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


@lru_cache
def get_order_writer() -> OrderWriter:
    return OrderWriter()
