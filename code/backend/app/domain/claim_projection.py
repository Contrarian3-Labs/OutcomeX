from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import Order, RevenueEntry, SettlementClaimRecord, SettlementRecord

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class OrderRefundClaimProjection:
    currency: str | None
    refundable_cents: int
    claimed_cents: int
    claimable_cents: int


@dataclass(frozen=True)
class RevenueEntryClaimProjection:
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


def project_machine_entry_claims(*, machine_id: str, db: Session) -> dict[str, RevenueEntryClaimProjection]:
    entries = list(
        db.scalars(
            select(RevenueEntry)
            .where(RevenueEntry.machine_id == machine_id)
            .order_by(RevenueEntry.created_at.asc(), RevenueEntry.id.asc())
        )
    )
    claims = list(
        db.scalars(
            select(SettlementClaimRecord)
            .where(
                SettlementClaimRecord.machine_id == machine_id,
                SettlementClaimRecord.claim_kind == "machine_revenue",
            )
            .order_by(SettlementClaimRecord.claimed_at.asc(), SettlementClaimRecord.id.asc())
        )
    )
    remaining_by_claimant: dict[str, int] = {}
    for claim in claims:
        if claim.claimant_user_id is None:
            continue
        remaining_by_claimant[claim.claimant_user_id] = (
            remaining_by_claimant.get(claim.claimant_user_id, 0) + claim.amount_cents
        )

    projection: dict[str, RevenueEntryClaimProjection] = {}
    for entry in entries:
        remaining = remaining_by_claimant.get(entry.beneficiary_user_id, 0)
        claimed_cents = min(entry.machine_share_cents, remaining)
        remaining_by_claimant[entry.beneficiary_user_id] = max(0, remaining - claimed_cents)
        projection[entry.id] = RevenueEntryClaimProjection(
            claimed_cents=claimed_cents,
            claimable_cents=max(0, entry.machine_share_cents - claimed_cents),
        )
    return projection


def project_platform_revenue_overview(*, currency: str, db: Session) -> tuple[int, int]:
    normalized_currency = currency.upper()
    rows = list(
        db.execute(
            select(Order, SettlementRecord)
            .join(SettlementRecord, SettlementRecord.order_id == Order.id)
            .where(Order.settlement_state == SettlementState.DISTRIBUTED)
            .order_by(SettlementRecord.distributed_at.asc(), Order.created_at.asc(), Order.id.asc())
        )
    )
    projected_cents = 0
    for order, settlement in rows:
        order_currency = order.latest_success_payment_currency.upper() if order.latest_success_payment_currency else None
        if order_currency == normalized_currency:
            projected_cents += settlement.platform_fee_cents
    claimed_cents = sum(
        record.amount_cents
        for record in db.scalars(
            select(SettlementClaimRecord)
            .where(SettlementClaimRecord.claim_kind == "platform_revenue")
            .order_by(SettlementClaimRecord.claimed_at.asc(), SettlementClaimRecord.id.asc())
        )
        if _claim_record_matches_currency(record.token_address, normalized_currency)
    )
    return projected_cents, claimed_cents


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
