from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState, SettlementState
from app.domain.models import Machine, MachineRevenueClaim, Order, Payment, RevenueEntry, SettlementClaimRecord, SettlementRecord
from app.domain.rules import has_sufficient_payment
from app.schemas.revenue import (
    RevenueClaimHistoryItem,
    RevenueAccountOverviewResponse,
    RevenueDistributionResponse,
    RevenueEntryResponse,
)

router = APIRouter()

def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _sum_projected_cents_for_beneficiary(*, beneficiary_user_id: str, db: Session) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.sum(RevenueEntry.machine_share_cents), 0)).where(
                RevenueEntry.beneficiary_user_id == beneficiary_user_id
            )
        )
        or 0
    )


def _sum_machine_claims_cents_for_claimant(*, claimant_user_id: str, db: Session) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.sum(SettlementClaimRecord.amount_cents), 0)).where(
                SettlementClaimRecord.claimant_user_id == claimant_user_id,
                SettlementClaimRecord.claim_kind == "machine_revenue",
            )
        )
        or 0
    )


def _user_paid_cents(*, user_id: str, db: Session) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.sum(Payment.amount_cents), 0))
            .join(Order, Order.id == Payment.order_id)
            .where(Order.user_id == user_id, Payment.state == PaymentState.SUCCEEDED)
        )
        or 0
    )


def _user_primary_currency(*, user_id: str, db: Session) -> str:
    currency = db.scalar(
        select(Payment.currency)
        .join(Order, Order.id == Payment.order_id)
        .where(Order.user_id == user_id, Payment.state == PaymentState.SUCCEEDED)
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return (currency or "USD").upper()


def _machine_claimable_cents(*, machine_id: str, db: Session) -> int:
    projected = (
        db.scalar(
            select(func.coalesce(func.sum(RevenueEntry.machine_share_cents), 0)).where(
                RevenueEntry.machine_id == machine_id
            )
        )
        or 0
    )
    claimed = (
        db.scalar(
            select(func.coalesce(func.sum(MachineRevenueClaim.amount_cents), 0)).where(
                MachineRevenueClaim.machine_id == machine_id
            )
        )
        or 0
    )
    return max(0, projected - claimed)


def _currency_from_token_address(token_address: str | None) -> str | None:
    if token_address is None:
        return None
    normalized = token_address.lower()
    if normalized == "0x0000000000000000000000000000000000000000":
        return "USDT"
    settings = get_settings()
    mapping = {
        settings.onchain_usdc_address.lower(): "USDC",
        settings.onchain_usdt_address.lower(): "USDT",
        settings.onchain_pwr_token_address.lower(): "PWR",
    }
    return mapping.get(normalized)


@router.post("/orders/{order_id}/distribute", response_model=RevenueDistributionResponse)
def distribute_revenue(order_id: str, db: Session = Depends(get_db)) -> RevenueDistributionResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()

    paid_cents = _succeeded_payment_total_cents(order.id, db)
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    if not has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Distribution requires full successful payment",
        )

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if (
        order.settlement_beneficiary_user_id is None
        or order.settlement_is_self_use is None
        or order.settlement_is_dividend_eligible is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement policy must be frozen before distribution",
        )

    dividend_eligible = order.settlement_is_dividend_eligible
    self_use = order.settlement_is_self_use
    entry = db.scalar(select(RevenueEntry).where(RevenueEntry.order_id == order.id))
    if settlement is None or entry is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement projection pending from indexed onchain events",
        )

    return RevenueDistributionResponse(
        order_id=order.id,
        settlement_id=settlement.id,
        machine_id=machine.id,
        beneficiary_user_id=entry.beneficiary_user_id,
        gross_amount_cents=entry.gross_amount_cents,
        platform_fee_cents=entry.platform_fee_cents,
        machine_share_cents=entry.machine_share_cents,
        is_self_use=self_use,
        is_dividend_eligible=dividend_eligible,
        distributed_at=settlement.distributed_at,
    )


@router.get("/machines/{machine_id}", response_model=list[RevenueEntryResponse])
def list_machine_revenue(machine_id: str, db: Session = Depends(get_db)) -> list[RevenueEntry]:
    return list(
        db.scalars(
            select(RevenueEntry)
            .where(RevenueEntry.machine_id == machine_id)
            .order_by(RevenueEntry.created_at.desc())
        )
    )


@router.get("/accounts/{owner_user_id}/overview", response_model=RevenueAccountOverviewResponse)
def revenue_account_overview(owner_user_id: str, db: Session = Depends(get_db)) -> RevenueAccountOverviewResponse:
    projected_cents = _sum_projected_cents_for_beneficiary(beneficiary_user_id=owner_user_id, db=db)
    claimed_cents = _sum_machine_claims_cents_for_claimant(claimant_user_id=owner_user_id, db=db)
    claimable_cents = max(0, projected_cents - claimed_cents)
    withdraw_history = list(
        db.scalars(
            select(SettlementClaimRecord)
            .where(
                SettlementClaimRecord.claimant_user_id == owner_user_id,
                SettlementClaimRecord.claim_kind == "machine_revenue",
            )
            .order_by(SettlementClaimRecord.claimed_at.desc(), SettlementClaimRecord.id.desc())
        )
    )
    return RevenueAccountOverviewResponse(
        owner_user_id=owner_user_id,
        paid_cents=_user_paid_cents(user_id=owner_user_id, db=db),
        projected_cents=projected_cents,
        claimable_cents=claimable_cents,
        claimed_cents=claimed_cents,
        currency=_user_primary_currency(user_id=owner_user_id, db=db),
        withdraw_history=[
            {
                "id": record.id,
                "machine_id": record.machine_id,
                "amount_cents": record.amount_cents,
                "tx_hash": record.tx_hash,
                "claimed_at": record.claimed_at,
            }
            for record in withdraw_history
        ],
    )


@router.get("/accounts/{user_id}/claims", response_model=list[RevenueClaimHistoryItem])
def list_revenue_claims(user_id: str, db: Session = Depends(get_db)) -> list[RevenueClaimHistoryItem]:
    records = list(
        db.scalars(
            select(SettlementClaimRecord)
            .where(SettlementClaimRecord.claimant_user_id == user_id)
            .order_by(SettlementClaimRecord.claimed_at.desc(), SettlementClaimRecord.id.desc())
        )
    )
    return [
        RevenueClaimHistoryItem(
            id=record.id,
            claim_kind=record.claim_kind,
            claimant_user_id=record.claimant_user_id,
            account_address=record.account_address,
            token_address=record.token_address,
            currency=_currency_from_token_address(record.token_address),
            amount_cents=record.amount_cents,
            tx_hash=record.tx_hash,
            machine_id=record.machine_id,
            claimed_at=record.claimed_at,
        )
        for record in records
    ]


