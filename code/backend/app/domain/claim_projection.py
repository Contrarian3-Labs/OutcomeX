from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.enums import OrderState, SettlementState
from app.domain.models import Order, RevenueEntry, SettlementClaimRecord, SettlementRecord
from app.domain.pwr_amounts import (
    parse_pwr_wei,
    pwr_payment_terms,
    rejected_valid_preview_pwr_split,
)
from app.domain.revenue_amounts import latest_success_payment, revenue_entry_machine_share_wei, settlement_claim_amount_wei

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class OrderRefundClaimProjection:
    currency: str | None
    refundable_cents: int
    claimed_cents: int
    claimable_cents: int
    pwr_anchor_price_cents: int | None = None
    refundable_amount_wei: str | None = None
    claimed_amount_wei: str | None = None
    claimable_amount_wei: str | None = None


@dataclass(frozen=True)
class RevenueEntryClaimProjection:
    claimed_cents: int
    claimable_cents: int
    claimed_amount_wei: str | None = None
    claimable_amount_wei: str | None = None


def project_order_refund_claim(*, order: Order, db: Session) -> OrderRefundClaimProjection:
    currency = order.latest_success_payment_currency.upper() if order.latest_success_payment_currency else None
    if (
        currency is None
        or order.state != OrderState.CANCELLED
        or order.settlement_state != SettlementState.DISTRIBUTED
    ):
        return OrderRefundClaimProjection(
            currency=currency,
            refundable_cents=0,
            claimed_cents=0,
            claimable_cents=0,
            refundable_amount_wei="0" if currency == "PWR" else None,
            claimed_amount_wei="0" if currency == "PWR" else None,
            claimable_amount_wei="0" if currency == "PWR" else None,
        )

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
    claimed_total_wei = refund_claimed_total_for_currency_wei(
        claimant_user_id=order.user_id,
        currency=currency,
        db=db,
    )

    remaining_claimed = int(claimed_total_cents)
    remaining_claimed_wei = int(claimed_total_wei)
    for candidate_order, settlement in rows:
        candidate_currency = (
            candidate_order.latest_success_payment_currency.upper()
            if candidate_order.latest_success_payment_currency
            else None
        )
        if candidate_currency != currency:
            continue
        refundable_cents, pwr_anchor_price_cents, refundable_amount_wei = _project_refundable_cents_for_order(
            order=candidate_order,
            settlement=settlement,
            db=db,
        )
        if refundable_cents <= 0:
            continue

        allocated_cents = min(refundable_cents, remaining_claimed)
        allocated_wei = min(parse_pwr_wei(refundable_amount_wei), remaining_claimed_wei)
        if candidate_order.id == order.id:
            return OrderRefundClaimProjection(
                currency=currency,
                refundable_cents=refundable_cents,
                claimed_cents=allocated_cents,
                claimable_cents=max(0, refundable_cents - allocated_cents),
                pwr_anchor_price_cents=pwr_anchor_price_cents,
                refundable_amount_wei=refundable_amount_wei,
                claimed_amount_wei=str(allocated_wei) if refundable_amount_wei is not None else None,
                claimable_amount_wei=(
                    str(max(0, parse_pwr_wei(refundable_amount_wei) - allocated_wei))
                    if refundable_amount_wei is not None
                    else None
                ),
            )
        remaining_claimed = max(0, remaining_claimed - refundable_cents)
        remaining_claimed_wei = max(0, remaining_claimed_wei - parse_pwr_wei(refundable_amount_wei))

    return OrderRefundClaimProjection(
        currency=currency,
        refundable_cents=0,
        claimed_cents=0,
        claimable_cents=0,
        refundable_amount_wei="0" if currency == "PWR" else None,
        claimed_amount_wei="0" if currency == "PWR" else None,
        claimable_amount_wei="0" if currency == "PWR" else None,
    )


def refund_claimed_total_for_currency(*, claimant_user_id: str, currency: str, db: Session) -> int:
    rows = db.scalars(
        select(SettlementClaimRecord).where(
            SettlementClaimRecord.claimant_user_id == claimant_user_id,
            SettlementClaimRecord.claim_kind == "refund",
        )
    )
    return sum(record.amount_cents for record in rows if _claim_record_matches_currency(record.token_address, currency))


def refund_claimed_total_for_currency_wei(*, claimant_user_id: str, currency: str, db: Session) -> int:
    if currency.upper() != "PWR":
        return 0
    rows = db.scalars(
        select(SettlementClaimRecord).where(
            SettlementClaimRecord.claimant_user_id == claimant_user_id,
            SettlementClaimRecord.claim_kind == "refund",
        )
    )
    return sum(
        _claim_record_amount_wei(record)
        for record in rows
        if _claim_record_matches_currency(record.token_address, currency)
    )


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
    remaining_by_claimant_wei: dict[str, int] = {}
    for claim in claims:
        if claim.claimant_user_id is None:
            continue
        remaining_by_claimant[claim.claimant_user_id] = (
            remaining_by_claimant.get(claim.claimant_user_id, 0) + claim.amount_cents
        )
        remaining_by_claimant_wei[claim.claimant_user_id] = (
            remaining_by_claimant_wei.get(claim.claimant_user_id, 0) + _claim_record_amount_wei(claim)
        )

    projection: dict[str, RevenueEntryClaimProjection] = {}
    for entry in entries:
        remaining = remaining_by_claimant.get(entry.beneficiary_user_id, 0)
        remaining_wei = remaining_by_claimant_wei.get(entry.beneficiary_user_id, 0)
        claimed_cents = min(entry.machine_share_cents, remaining)
        entry_amount_wei = _entry_machine_share_wei(entry=entry, db=db)
        claimed_amount_wei = min(entry_amount_wei, remaining_wei)
        remaining_by_claimant[entry.beneficiary_user_id] = max(0, remaining - claimed_cents)
        remaining_by_claimant_wei[entry.beneficiary_user_id] = max(0, remaining_wei - claimed_amount_wei)
        projection[entry.id] = RevenueEntryClaimProjection(
            claimed_cents=claimed_cents,
            claimable_cents=max(0, entry.machine_share_cents - claimed_cents),
            claimed_amount_wei=str(claimed_amount_wei),
            claimable_amount_wei=str(max(0, entry_amount_wei - claimed_amount_wei)),
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


def _project_refundable_cents_for_order(
    *,
    order: Order,
    settlement: SettlementRecord,
    db: Session,
) -> tuple[int, int | None, str | None]:
    currency = order.latest_success_payment_currency.upper() if order.latest_success_payment_currency else None
    if currency != "PWR":
        return _refund_due_cents(settlement), None, None

    payment = latest_success_payment(order_id=order.id, db=db)
    pwr_amount_wei, pwr_anchor_price_cents = pwr_payment_terms(payment)
    if pwr_amount_wei is None:
        return _refund_due_cents(settlement), pwr_anchor_price_cents, None

    refundable_amount_wei = pwr_amount_wei
    if settlement.machine_share_cents > 0 or settlement.platform_fee_cents > 0:
        refundable_amount_wei, _, _ = rejected_valid_preview_pwr_split(pwr_amount_wei)
    return _refund_due_cents(settlement), pwr_anchor_price_cents, str(refundable_amount_wei)


def _refund_due_cents(settlement: SettlementRecord) -> int:
    return max(0, settlement.gross_amount_cents - settlement.platform_fee_cents - settlement.machine_share_cents)


def _claim_record_amount_wei(record: SettlementClaimRecord) -> int:
    if record.claim_kind == "machine_revenue":
        return settlement_claim_amount_wei(record)
    if _claim_record_matches_currency(record.token_address, "PWR"):
        return settlement_claim_amount_wei(record)
    return 0


def _entry_machine_share_wei(*, entry: RevenueEntry, db: Session) -> int:
    return revenue_entry_machine_share_wei(entry=entry, db=db)


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
