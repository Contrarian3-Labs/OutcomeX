from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import Order, SettlementClaimRecord, SettlementRecord

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class OrderRefundClaimProjection:
    currency: str | None
    refundable_cents: int
    claimed_cents: int
    claimable_cents: int


def project_order_refund_claim(*, order: Order, db: Session) -> OrderRefundClaimProjection:
    currency = order.latest_success_payment_currency.upper() if order.latest_success_payment_currency else None
    if (
        currency is None
        or order.state != OrderState.CANCELLED
        or order.settlement_state != SettlementState.DISTRIBUTED
    ):
        return OrderRefundClaimProjection(currency=currency, refundable_cents=0, claimed_cents=0, claimable_cents=0)

    rows = list(
        db.execute(
            select(Order, SettlementRecord)
            .join(SettlementRecord, SettlementRecord.order_id == Order.id)
            .where(
                Order.user_id == order.user_id,
                Order.state == OrderState.CANCELLED,
                Order.settlement_state == SettlementState.DISTRIBUTED,
            )
            .order_by(SettlementRecord.distributed_at.asc(), Order.created_at.asc(), Order.id.asc())
        )
    )
    claimed_total_cents = refund_claimed_total_for_currency(
        claimant_user_id=order.user_id,
        currency=currency,
        db=db,
    )

    remaining_claimed = int(claimed_total_cents)
    for candidate_order, settlement in rows:
        candidate_currency = (
            candidate_order.latest_success_payment_currency.upper()
            if candidate_order.latest_success_payment_currency
            else None
        )
        if candidate_currency != currency:
            continue
        refundable_cents = _refund_due_cents(settlement)
        if refundable_cents <= 0:
            continue

        allocated_cents = min(refundable_cents, remaining_claimed)
        if candidate_order.id == order.id:
            return OrderRefundClaimProjection(
                currency=currency,
                refundable_cents=refundable_cents,
                claimed_cents=allocated_cents,
                claimable_cents=max(0, refundable_cents - allocated_cents),
            )
        remaining_claimed = max(0, remaining_claimed - refundable_cents)

    return OrderRefundClaimProjection(currency=currency, refundable_cents=0, claimed_cents=0, claimable_cents=0)


def refund_claimed_total_for_currency(*, claimant_user_id: str, currency: str, db: Session) -> int:
    rows = db.scalars(
        select(SettlementClaimRecord).where(
            SettlementClaimRecord.claimant_user_id == claimant_user_id,
            SettlementClaimRecord.claim_kind == "refund",
        )
    )
    return sum(record.amount_cents for record in rows if _claim_record_matches_currency(record.token_address, currency))


def _refund_due_cents(settlement: SettlementRecord) -> int:
    return max(0, settlement.gross_amount_cents - settlement.platform_fee_cents - settlement.machine_share_cents)


def _claim_record_matches_currency(token_address: str | None, currency: str) -> bool:
    settings = get_settings()
    normalized = (token_address or ZERO_ADDRESS).lower()
    wanted = currency.upper()
    if wanted == "USDC":
        return normalized == settings.onchain_usdc_address.lower()
    if wanted == "USDT":
        return normalized in {ZERO_ADDRESS, settings.onchain_usdt_address.lower()}
    if wanted == "PWR":
        return normalized == settings.onchain_pwr_token_address.lower()
    return False
