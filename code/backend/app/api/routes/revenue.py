from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.claim_projection import project_machine_entry_claims, project_platform_revenue_overview
from app.domain.enums import PaymentState
from app.domain.models import (
    Machine,
    MachineListing,
    MachineRevenueClaim,
    Order,
    Payment,
    PrimaryIssuancePurchase,
    RevenueEntry,
    SettlementClaimRecord,
    SettlementRecord,
)
from app.domain.pwr_amounts import pwr_wei_to_float
from app.domain.revenue_amounts import (
    machine_revenue_claim_amount_wei,
    revenue_entry_machine_share_wei,
    settlement_claim_amount_wei,
)
from app.domain.rules import has_sufficient_payment
from app.runtime.cost_service import get_runtime_cost_service
from app.schemas.revenue import (
    PaymentLedgerItem,
    PlatformRevenueOverviewResponse,
    RevenueAccountAnalyticsResponse,
    RevenueClaimHistoryItem,
    RevenueAccountOverviewResponse,
    RevenueAnalyticsPoint,
    RevenueDistributionResponse,
    RevenueEntryResponse,
    RevenueMachineBreakdownItem,
)

router = APIRouter()

DEFAULT_MACHINE_ASSET_COST_CENTS = 399_900
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

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


def _sum_projected_pwr_wei_for_beneficiary(*, beneficiary_user_id: str, db: Session) -> int:
    entries = db.scalars(
        select(RevenueEntry).where(RevenueEntry.beneficiary_user_id == beneficiary_user_id)
    ).all()
    return sum(revenue_entry_machine_share_wei(entry=entry, db=db) for entry in entries)


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


def _sum_machine_claims_wei_for_claimant(*, claimant_user_id: str, db: Session) -> int:
    records = db.scalars(
        select(SettlementClaimRecord).where(
            SettlementClaimRecord.claimant_user_id == claimant_user_id,
            SettlementClaimRecord.claim_kind == "machine_revenue",
        )
    ).all()
    return sum(settlement_claim_amount_wei(record) for record in records)


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


def _machine_claimable_pwr_wei(*, machine_id: str, db: Session) -> int:
    projected = sum(
        revenue_entry_machine_share_wei(entry=entry, db=db)
        for entry in db.scalars(select(RevenueEntry).where(RevenueEntry.machine_id == machine_id)).all()
    )
    claimed = sum(
        machine_revenue_claim_amount_wei(claim)
        for claim in db.scalars(select(MachineRevenueClaim).where(MachineRevenueClaim.machine_id == machine_id)).all()
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


def _claim_record_currency(*, claim_kind: str, token_address: str | None) -> str | None:
    if claim_kind == "machine_revenue":
        return _currency_from_token_address(token_address) or "PWR"
    return _currency_from_token_address(token_address)


def _day_start_utc(value: datetime) -> datetime:
    normalized = value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime(normalized.year, normalized.month, normalized.day, tzinfo=timezone.utc)


def _day_key(value: datetime) -> str:
    return _day_start_utc(value).date().isoformat()


def _price_units_to_cents(*, price_units: int, decimals: int | None, token_symbol: str | None, token_address: str | None) -> int | None:
    normalized_currency = (token_symbol or _currency_from_token_address(token_address) or "").upper()
    resolved_decimals = decimals if decimals is not None else (18 if normalized_currency == "PWR" else 6)
    if resolved_decimals < 0:
        return None

    if normalized_currency in {"USDC", "USDT"}:
        amount_cents = (Decimal(price_units) * Decimal(100)) / (Decimal(10) ** resolved_decimals)
        return int(amount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if normalized_currency == "PWR":
        anchor = get_runtime_cost_service().pwr_anchor_price_cents
        amount_cents = (Decimal(price_units) * Decimal(anchor)) / (Decimal(10) ** resolved_decimals)
        return int(amount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if token_address and token_address.lower() == ZERO_ADDRESS:
        amount_cents = (Decimal(price_units) * Decimal(100)) / (Decimal(10) ** resolved_decimals)
        return int(amount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return None


def _machine_acquisition_price_cents(*, machine: Machine, db: Session) -> int:
    primary_purchase = db.scalar(
        select(PrimaryIssuancePurchase)
        .where(
            PrimaryIssuancePurchase.minted_machine_id == machine.id,
            PrimaryIssuancePurchase.state == PaymentState.SUCCEEDED,
        )
        .order_by(PrimaryIssuancePurchase.created_at.desc())
        .limit(1)
    )
    if primary_purchase is not None and primary_purchase.amount_cents > 0:
        return primary_purchase.amount_cents

    listing = db.scalar(
        select(MachineListing)
        .where(
            MachineListing.machine_id == machine.id,
            MachineListing.filled_at.is_not(None),
        )
        .order_by(MachineListing.filled_at.desc(), MachineListing.updated_at.desc())
        .limit(1)
    )
    if listing is not None:
        listing_price_cents = _price_units_to_cents(
            price_units=int(listing.price_units),
            decimals=listing.payment_token_decimals,
            token_symbol=listing.payment_token_symbol,
            token_address=listing.payment_token_address,
        )
        if listing_price_cents is not None and listing_price_cents > 0:
            return listing_price_cents

    return DEFAULT_MACHINE_ASSET_COST_CENTS


def _build_revenue_series(*, owner_user_id: str, days: int, db: Session) -> list[RevenueAnalyticsPoint]:
    now = datetime.now(timezone.utc)
    today = _day_start_utc(now)
    start = today - timedelta(days=days - 1)
    totals_by_day = {(_day_start_utc(start + timedelta(days=offset))).date().isoformat(): 0 for offset in range(days)}
    totals_pwr_wei_by_day = {date_key: 0 for date_key in totals_by_day}

    rows = db.scalars(
        select(RevenueEntry)
        .where(
            RevenueEntry.beneficiary_user_id == owner_user_id,
            RevenueEntry.created_at >= start,
        )
        .order_by(RevenueEntry.created_at.asc(), RevenueEntry.id.asc())
    ).all()

    for entry in rows:
        date_key = _day_key(entry.created_at)
        if date_key in totals_by_day:
            totals_by_day[date_key] += entry.machine_share_cents
            totals_pwr_wei_by_day[date_key] += revenue_entry_machine_share_wei(entry=entry, db=db)

    return [
        RevenueAnalyticsPoint(
            date_key=date_key,
            amount_cents=amount_cents,
            amount_pwr=pwr_wei_to_float(totals_pwr_wei_by_day[date_key]),
        )
        for date_key, amount_cents in totals_by_day.items()
    ]


def _machine_breakdown(*, owner_user_id: str, db: Session) -> tuple[list[RevenueMachineBreakdownItem], int]:
    entries = db.scalars(
        select(RevenueEntry)
        .where(RevenueEntry.beneficiary_user_id == owner_user_id)
        .order_by(RevenueEntry.created_at.desc(), RevenueEntry.id.desc())
    ).all()
    if not entries:
        return [], 0

    totals_by_machine: dict[str, int] = defaultdict(int)
    totals_pwr_wei_by_machine: dict[str, int] = defaultdict(int)
    for entry in entries:
        totals_by_machine[entry.machine_id] += entry.machine_share_cents
        totals_pwr_wei_by_machine[entry.machine_id] += revenue_entry_machine_share_wei(entry=entry, db=db)

    claimed_by_machine_rows = db.execute(
        select(
            SettlementClaimRecord.machine_id,
            func.coalesce(func.sum(SettlementClaimRecord.amount_cents), 0),
        )
        .where(
            SettlementClaimRecord.claimant_user_id == owner_user_id,
            SettlementClaimRecord.claim_kind == "machine_revenue",
            SettlementClaimRecord.machine_id.is_not(None),
            SettlementClaimRecord.machine_id.in_(list(totals_by_machine.keys())),
        )
        .group_by(SettlementClaimRecord.machine_id)
    ).all()
    claimed_by_machine = {machine_id: int(amount_cents or 0) for machine_id, amount_cents in claimed_by_machine_rows}
    claimed_by_machine_pwr_wei: dict[str, int] = defaultdict(int)
    claim_rows = db.scalars(
        select(SettlementClaimRecord).where(
            SettlementClaimRecord.claimant_user_id == owner_user_id,
            SettlementClaimRecord.claim_kind == "machine_revenue",
            SettlementClaimRecord.machine_id.is_not(None),
            SettlementClaimRecord.machine_id.in_(list(totals_by_machine.keys())),
        )
    ).all()
    for record in claim_rows:
        if record.machine_id is not None:
            claimed_by_machine_pwr_wei[record.machine_id] += settlement_claim_amount_wei(record)

    machine_rows = db.scalars(select(Machine).where(Machine.id.in_(list(totals_by_machine.keys())))).all()
    machines_by_id = {machine.id: machine for machine in machine_rows}

    breakdown: list[RevenueMachineBreakdownItem] = []
    acquisition_total_cents = 0
    for machine_id, total_earned_cents in sorted(totals_by_machine.items(), key=lambda item: (-item[1], item[0])):
        machine = machines_by_id.get(machine_id)
        claimable_cents = max(0, total_earned_cents - claimed_by_machine.get(machine_id, 0))
        acquisition_price_cents = _machine_acquisition_price_cents(machine=machine, db=db) if machine is not None else DEFAULT_MACHINE_ASSET_COST_CENTS
        acquisition_total_cents += acquisition_price_cents
        breakdown.append(
            RevenueMachineBreakdownItem(
                machine_id=machine_id,
                display_name=machine.display_name if machine is not None else machine_id,
                total_earned_cents=total_earned_cents,
                claimable_cents=claimable_cents,
                total_earned_pwr=pwr_wei_to_float(totals_pwr_wei_by_machine[machine_id]),
                claimable_pwr=pwr_wei_to_float(
                    max(0, totals_pwr_wei_by_machine[machine_id] - claimed_by_machine_pwr_wei.get(machine_id, 0))
                ),
                acquisition_price_cents=acquisition_price_cents,
            )
        )
    return breakdown, acquisition_total_cents


def _sum_series_pwr(points: list[RevenueAnalyticsPoint]) -> float:
    return round(sum(point.amount_pwr or 0 for point in points), 4)


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
        machine_share_pwr=pwr_wei_to_float(revenue_entry_machine_share_wei(entry=entry, db=db)),
        is_self_use=self_use,
        is_dividend_eligible=dividend_eligible,
        distributed_at=settlement.distributed_at,
    )


@router.get("/machines/{machine_id}", response_model=list[RevenueEntryResponse])
def list_machine_revenue(machine_id: str, db: Session = Depends(get_db)) -> list[RevenueEntryResponse]:
    entries = list(
        db.scalars(
            select(RevenueEntry)
            .where(RevenueEntry.machine_id == machine_id)
            .order_by(RevenueEntry.created_at.desc(), RevenueEntry.id.desc())
        )
    )
    projection = project_machine_entry_claims(machine_id=machine_id, db=db)
    return [
        RevenueEntryResponse.model_validate(entry).model_copy(
            update={
                "claimed_cents": projection.get(entry.id).claimed_cents if projection.get(entry.id) else 0,
                "claimable_cents": projection.get(entry.id).claimable_cents if projection.get(entry.id) else entry.machine_share_cents,
                "machine_share_pwr": pwr_wei_to_float(revenue_entry_machine_share_wei(entry=entry, db=db)),
                "claimed_pwr": pwr_wei_to_float(projection.get(entry.id).claimed_amount_wei if projection.get(entry.id) else "0"),
                "claimable_pwr": pwr_wei_to_float(
                    projection.get(entry.id).claimable_amount_wei
                    if projection.get(entry.id)
                    else revenue_entry_machine_share_wei(entry=entry, db=db)
                ),
            }
        )
        for entry in entries
    ]


@router.get("/accounts/{owner_user_id}/overview", response_model=RevenueAccountOverviewResponse)
def revenue_account_overview(owner_user_id: str, db: Session = Depends(get_db)) -> RevenueAccountOverviewResponse:
    projected_cents = _sum_projected_cents_for_beneficiary(beneficiary_user_id=owner_user_id, db=db)
    claimed_cents = _sum_machine_claims_cents_for_claimant(claimant_user_id=owner_user_id, db=db)
    claimable_cents = max(0, projected_cents - claimed_cents)
    projected_pwr_wei = _sum_projected_pwr_wei_for_beneficiary(beneficiary_user_id=owner_user_id, db=db)
    claimed_pwr_wei = _sum_machine_claims_wei_for_claimant(claimant_user_id=owner_user_id, db=db)
    claimable_pwr_wei = max(0, projected_pwr_wei - claimed_pwr_wei)
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
    currency = (
        "PWR"
        if (projected_pwr_wei > 0 or claimed_pwr_wei > 0 or claimable_pwr_wei > 0 or projected_cents > 0 or claimed_cents > 0 or claimable_cents > 0)
        else _user_primary_currency(user_id=owner_user_id, db=db)
    )
    return RevenueAccountOverviewResponse(
        owner_user_id=owner_user_id,
        paid_cents=_user_paid_cents(user_id=owner_user_id, db=db),
        projected_cents=projected_cents,
        claimable_cents=claimable_cents,
        claimed_cents=claimed_cents,
        projected_pwr=pwr_wei_to_float(projected_pwr_wei) if currency == "PWR" else None,
        claimable_pwr=pwr_wei_to_float(claimable_pwr_wei) if currency == "PWR" else None,
        claimed_pwr=pwr_wei_to_float(claimed_pwr_wei) if currency == "PWR" else None,
        currency=currency,
        pwr_anchor_price_cents=(get_runtime_cost_service().pwr_anchor_price_cents if currency == "PWR" else None),
        withdraw_history=[
            {
                "id": record.id,
                "machine_id": record.machine_id,
                "amount_cents": record.amount_cents,
                "amount_pwr": pwr_wei_to_float(settlement_claim_amount_wei(record))
                if _claim_record_currency(claim_kind=record.claim_kind, token_address=record.token_address) == "PWR"
                else None,
                "tx_hash": record.tx_hash,
                "claimed_at": record.claimed_at,
            }
            for record in withdraw_history
        ],
    )


@router.get("/accounts/{owner_user_id}/analytics", response_model=RevenueAccountAnalyticsResponse)
def revenue_account_analytics(owner_user_id: str, db: Session = Depends(get_db)) -> RevenueAccountAnalyticsResponse:
    projected_cents = _sum_projected_cents_for_beneficiary(beneficiary_user_id=owner_user_id, db=db)
    claimed_cents = _sum_machine_claims_cents_for_claimant(claimant_user_id=owner_user_id, db=db)
    claimable_cents = max(0, projected_cents - claimed_cents)
    projected_pwr_wei = _sum_projected_pwr_wei_for_beneficiary(beneficiary_user_id=owner_user_id, db=db)
    claimed_pwr_wei = _sum_machine_claims_wei_for_claimant(claimant_user_id=owner_user_id, db=db)
    claimable_pwr_wei = max(0, projected_pwr_wei - claimed_pwr_wei)
    currency = (
        "PWR"
        if (projected_pwr_wei > 0 or claimed_pwr_wei > 0 or claimable_pwr_wei > 0 or projected_cents > 0 or claimed_cents > 0 or claimable_cents > 0)
        else _user_primary_currency(user_id=owner_user_id, db=db)
    )
    pwr_anchor_price_cents = get_runtime_cost_service().pwr_anchor_price_cents if currency == "PWR" else None
    series_7d = _build_revenue_series(owner_user_id=owner_user_id, days=7, db=db)
    series_30d = _build_revenue_series(owner_user_id=owner_user_id, days=30, db=db)
    series_90d = _build_revenue_series(owner_user_id=owner_user_id, days=90, db=db)
    last_7d_cents = sum(item.amount_cents for item in series_7d)
    trailing_30d_cents = sum(item.amount_cents for item in series_30d)
    machine_breakdown, acquisition_total_cents = _machine_breakdown(owner_user_id=owner_user_id, db=db)
    indicative_apr = round((trailing_30d_cents * 12 * 100) / acquisition_total_cents, 2) if acquisition_total_cents > 0 else 0.0

    return RevenueAccountAnalyticsResponse(
        owner_user_id=owner_user_id,
        currency=currency,
        total_earned_cents=projected_cents,
        claimable_cents=claimable_cents,
        claimed_cents=claimed_cents,
        last_7d_cents=last_7d_cents,
        trailing_30d_cents=trailing_30d_cents,
        total_earned_pwr=pwr_wei_to_float(projected_pwr_wei) if currency == "PWR" else None,
        claimable_pwr=pwr_wei_to_float(claimable_pwr_wei) if currency == "PWR" else None,
        claimed_pwr=pwr_wei_to_float(claimed_pwr_wei) if currency == "PWR" else None,
        last_7d_pwr=_sum_series_pwr(series_7d) if currency == "PWR" else None,
        trailing_30d_pwr=_sum_series_pwr(series_30d) if currency == "PWR" else None,
        indicative_apr=indicative_apr,
        acquisition_total_cents=acquisition_total_cents,
        pwr_anchor_price_cents=pwr_anchor_price_cents,
        series_7d=series_7d,
        series_30d=series_30d,
        series_90d=series_90d,
        machine_breakdown=machine_breakdown,
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
            currency=_claim_record_currency(claim_kind=record.claim_kind, token_address=record.token_address),
            amount_cents=record.amount_cents,
            amount_pwr=(
                pwr_wei_to_float(settlement_claim_amount_wei(record))
                if _claim_record_currency(claim_kind=record.claim_kind, token_address=record.token_address) == "PWR"
                else None
            ),
            tx_hash=record.tx_hash,
            machine_id=record.machine_id,
            claimed_at=record.claimed_at,
        )
        for record in records
    ]


@router.get("/accounts/{user_id}/payment-ledger", response_model=list[PaymentLedgerItem])
def list_payment_ledger(user_id: str, db: Session = Depends(get_db)) -> list[PaymentLedgerItem]:
    rows = db.execute(
        select(Payment, Order.user_prompt)
        .join(Order, Order.id == Payment.order_id)
        .where(Order.user_id == user_id)
        .order_by(Payment.created_at.desc(), Payment.id.desc())
    ).all()
    return [
        PaymentLedgerItem(
            payment_id=payment.id,
            order_id=payment.order_id,
            user_prompt=user_prompt,
            provider=payment.provider,
            provider_reference=payment.provider_reference,
            currency=payment.currency.upper(),
            amount_cents=payment.amount_cents,
            state=payment.state.value,
            tx_hash=payment.callback_tx_hash,
            created_at=payment.created_at,
        )
        for payment, user_prompt in rows
    ]


@router.get("/platform/overview", response_model=PlatformRevenueOverviewResponse)
def platform_revenue_overview(
    currency: str,
    db: Session = Depends(get_db),
) -> PlatformRevenueOverviewResponse:
    normalized_currency = currency.upper()
    projected_cents, claimed_cents = project_platform_revenue_overview(currency=normalized_currency, db=db)
    claim_history = [
        RevenueClaimHistoryItem(
            id=record.id,
            claim_kind=record.claim_kind,
            claimant_user_id=record.claimant_user_id,
            account_address=record.account_address,
            token_address=record.token_address,
            currency=_claim_record_currency(claim_kind=record.claim_kind, token_address=record.token_address),
            amount_cents=record.amount_cents,
            amount_pwr=(
                pwr_wei_to_float(settlement_claim_amount_wei(record))
                if _claim_record_currency(claim_kind=record.claim_kind, token_address=record.token_address) == "PWR"
                else None
            ),
            tx_hash=record.tx_hash,
            machine_id=record.machine_id,
            claimed_at=record.claimed_at,
        )
        for record in db.scalars(
            select(SettlementClaimRecord)
            .where(SettlementClaimRecord.claim_kind == "platform_revenue")
            .order_by(SettlementClaimRecord.claimed_at.desc(), SettlementClaimRecord.id.desc())
        )
        if _currency_from_token_address(record.token_address) == normalized_currency
    ]
    return PlatformRevenueOverviewResponse(
        currency=normalized_currency,
        projected_cents=projected_cents,
        claimed_cents=claimed_cents,
        claimable_cents=max(0, projected_cents - claimed_cents),
        claim_history=claim_history,
    )
