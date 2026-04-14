from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from typing import Any

from app.domain.models import Machine, Order, Payment, SettlementRecord
from app.onchain.contracts_registry import ContractsRegistry


STABLECOIN_UNITS_PER_CENT = 10_000


def _payment_amount_onchain(*, currency: str, amount_cents: int) -> int:
    normalized = currency.upper()
    if normalized in {"USDC", "USDT"}:
        return amount_cents * STABLECOIN_UNITS_PER_CENT
    return amount_cents


@dataclass(frozen=True)
class OrderWriteResult:
    tx_hash: str
    submitted_at: datetime
    chain_id: int
    contract_name: str
    contract_address: str
    method_name: str
    idempotency_key: str
    payload: dict[str, Any]


class OrderWriter:
    def __init__(self, contracts_registry: ContractsRegistry | None = None) -> None:
        self._contracts_registry = contracts_registry or ContractsRegistry()

    def create_order(
        self,
        order: Order,
        *,
        buyer_wallet_address: str,
        gross_amount_override: int | None = None,
    ) -> OrderWriteResult:
        payload = {
            "buyer": buyer_wallet_address.lower(),
            "machine_id": self._chain_machine_id(order),
            "gross_amount": order.quoted_amount_cents if gross_amount_override is None else gross_amount_override,
        }
        return self._submit_to_target(
            self._contracts_registry.payment_router(),
            "createOrderByAdapter",
            payload,
            idempotency_scope={"client_order_id": order.id},
        )

    def mark_order_paid(self, order: Order, payment: Payment) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
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

    def pay_order_by_adapter(self, order: Order, payment: Payment) -> OrderWriteResult:
        currency = payment.currency.upper()
        payload = {
            "order_id": self._chain_order_id(order),
            "amount": _payment_amount_onchain(currency=currency, amount_cents=payment.amount_cents),
            "payment_token_address": self._contracts_registry.payment_token(currency),
        }
        return self._submit_to_target(
            self._contracts_registry.payment_router(),
            "payOrderByAdapter",
            payload,
            idempotency_scope={"client_order_id": order.id, "payment_id": payment.id},
        )

    def build_direct_payment_intent(
        self,
        order: Order,
        payment: Payment,
        *,
        pwr_amount: str | None = None,
        pricing_version: str | None = None,
        pwr_anchor_price_cents: int | None = None,
    ) -> OrderWriteResult:
        currency = payment.currency.upper()
        if currency == "USDC":
            method_name = "createOrderAndPayWithUSDC"
            signing_standard = "eip3009"
            payload = {
                "client_order_id": order.id,
                "machine_id": self._chain_machine_id(order),
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
                "currency": currency,
                "token_address": self._contracts_registry.payment_token(currency),
                "signing_standard": signing_standard,
            }
        elif currency == "USDT":
            method_name = "createOrderAndPayWithUSDT"
            signing_standard = "erc20_approve"
            payload = {
                "client_order_id": order.id,
                "machine_id": self._chain_machine_id(order),
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
                "currency": currency,
                "token_address": self._contracts_registry.payment_token(currency),
                "signing_standard": signing_standard,
            }
        elif currency == "PWR":
            if pwr_amount is None or pricing_version is None or pwr_anchor_price_cents is None:
                raise ValueError("pwr_amount_required")
            method_name = "payWithPWR"
            payload = {
                "client_order_id": order.id,
                "order_id": self._chain_order_id(order),
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
                "currency": "PWR",
                "token_address": self._contracts_registry.payment_token("PWR"),
                "signing_standard": "erc20_approve",
                "pwr_amount": pwr_amount,
                "pricing_version": pricing_version,
                "pwr_anchor_price_cents": pwr_anchor_price_cents,
            }
        else:
            raise ValueError(f"unsupported_direct_payment_currency:{currency}")

        return self._submit_to_target(self._contracts_registry.payment_router(), method_name, payload)

    def mark_preview_ready(self, order: Order, *, valid_preview: bool = True) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
            "valid_preview": valid_preview,
        }
        return self._submit("markPreviewReady", payload)

    def confirm_result(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
        }
        return self._submit("confirmResult", payload)

    def reject_valid_preview(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
        }
        return self._submit("rejectValidPreview", payload)

    def claim_machine_revenue(self, machine: Machine) -> OrderWriteResult:
        payload = {
            "machine_id": self._chain_machine_id_from_machine(machine),
        }
        return self._submit_to_target(
            self._contracts_registry.revenue_vault(),
            "claimMachineRevenue",
            payload,
            idempotency_scope={"machine_id": machine.id, "owner_user_id": machine.owner_user_id},
        )

    def claim_refund(self, *, currency: str, user_id: str, order_id: str) -> OrderWriteResult:
        payload = {
            "payment_token_address": self._contracts_registry.payment_token(currency),
        }
        return self._submit_to_target(
            self._contracts_registry.settlement_controller(),
            "claimRefund",
            payload,
            idempotency_scope={"user_id": user_id, "currency": currency.upper(), "order_id": order_id},
        )

    def claim_platform_revenue(self, *, currency: str) -> OrderWriteResult:
        payload = {
            "payment_token_address": self._contracts_registry.payment_token(currency),
        }
        return self._submit_to_target(
            self._contracts_registry.settlement_controller(),
            "claimPlatformRevenue",
            payload,
            idempotency_scope={"currency": currency.upper()},
        )

    def mint_machine(self, *, owner_wallet_address: str, token_uri: str, owner_user_id: str) -> OrderWriteResult:
        payload = {
            "to": owner_wallet_address.lower(),
            "uri": token_uri,
        }
        return self._submit_to_target(
            self._contracts_registry.machine_asset(),
            "mintMachine",
            payload,
            idempotency_scope={"owner_user_id": owner_user_id, "token_uri": token_uri},
        )

    def refund_failed_or_no_valid_preview(self, order: Order) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
        }
        return self._submit("refundFailedOrNoValidPreview", payload)

    def settle_order(self, order: Order, settlement: SettlementRecord) -> OrderWriteResult:
        payload = {
            "order_id": self._chain_order_id(order),
            "settlement_id": settlement.id,
            "gross_amount_cents": settlement.gross_amount_cents,
            "platform_fee_cents": settlement.platform_fee_cents,
            "machine_share_cents": settlement.machine_share_cents,
            "settlement_beneficiary_user_id": order.settlement_beneficiary_user_id,
            "settlement_is_dividend_eligible": order.settlement_is_dividend_eligible,
        }
        return self._submit("settleOrder", payload)

    def _submit(
        self,
        method_name: str,
        payload: dict[str, Any],
        *,
        idempotency_scope: dict[str, Any] | None = None,
    ) -> OrderWriteResult:
        return self._submit_to_target(
            self._contracts_registry.order_book(),
            method_name,
            payload,
            idempotency_scope=idempotency_scope,
        )

    def _submit_to_target(
        self,
        target: Any,
        method_name: str,
        payload: dict[str, Any],
        *,
        idempotency_scope: dict[str, Any] | None = None,
    ) -> OrderWriteResult:
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=self._json_default)
        canonical_scope = json.dumps(
            idempotency_scope or {},
            sort_keys=True,
            separators=(",", ":"),
            default=self._json_default,
        )
        idempotency_key = hashlib.sha256(
            f"{method_name}:{canonical_payload}:{canonical_scope}".encode("utf-8")
        ).hexdigest()
        tx_hash = "0x" + hashlib.sha256(
            f"{target.contract_name}:{target.contract_address}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return OrderWriteResult(
            tx_hash=tx_hash,
            submitted_at=datetime.now(timezone.utc),
            chain_id=target.chain_id,
            contract_name=target.contract_name,
            contract_address=target.contract_address,
            method_name=method_name,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _chain_order_id(order: Order) -> str:
        return order.onchain_order_id or order.id

    @staticmethod
    def _chain_machine_id(order: Order) -> str:
        return order.onchain_machine_id or order.machine_id

    @staticmethod
    def _chain_machine_id_from_machine(machine: Machine) -> str:
        return machine.onchain_machine_id or machine.id


@lru_cache
def get_order_writer() -> OrderWriter:
    return OrderWriter()
