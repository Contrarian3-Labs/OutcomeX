from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import PaymentState
from app.domain.models import MachineRevenueClaim, Payment, RevenueEntry, SettlementClaimRecord
from app.domain.pwr_amounts import (
    cents_to_pwr_wei,
    confirmed_pwr_split,
    parse_pwr_wei,
    pwr_payment_terms,
    rejected_valid_preview_pwr_split,
)


def latest_success_payment(*, order_id: str, db: Session) -> Payment | None:
    return db.scalar(
        select(Payment)
        .where(Payment.order_id == order_id, Payment.state == PaymentState.SUCCEEDED)
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(1)
    )


def revenue_entry_machine_share_wei(*, entry: RevenueEntry, db: Session) -> int:
    parsed = parse_pwr_wei(entry.machine_share_pwr_wei)
    if parsed > 0:
        return parsed
    if not entry.is_dividend_eligible:
        return 0

    payment = latest_success_payment(order_id=entry.order_id, db=db)
    pwr_amount_wei, _anchor = pwr_payment_terms(payment)
    if pwr_amount_wei is None:
        return cents_to_pwr_wei(entry.machine_share_cents)

    refundable_cents = max(0, entry.gross_amount_cents - entry.platform_fee_cents - entry.machine_share_cents)
    if refundable_cents > 0:
        _refund_wei, _platform_wei, machine_wei = rejected_valid_preview_pwr_split(pwr_amount_wei)
        return machine_wei

    _platform_wei, machine_wei = confirmed_pwr_split(pwr_amount_wei)
    return machine_wei


def settlement_claim_amount_wei(record: SettlementClaimRecord) -> int:
    parsed = parse_pwr_wei(record.amount_wei)
    if parsed > 0:
        return parsed
    return cents_to_pwr_wei(record.amount_cents)


def machine_revenue_claim_amount_wei(claim: MachineRevenueClaim) -> int:
    parsed = parse_pwr_wei(claim.amount_wei)
    if parsed > 0:
        return parsed
    return cents_to_pwr_wei(claim.amount_cents)
