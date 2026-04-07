from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState, SettlementState
from app.domain.models import Machine, MachineRevenueClaim, Order, Payment, RevenueEntry, SettlementRecord
from app.domain.settlement_projection import ensure_confirmed_settlement_projection
from app.domain.rules import has_sufficient_payment
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.tx_sender import encode_contract_call
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.schemas.revenue import (
    MachineRevenueClaimResponse,
    RevenueAccountOverviewResponse,
    RevenueDistributionResponse,
    RevenueEntryResponse,
)

router = APIRouter()


def _normalize_action_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in {"server_broadcast", "user_sign"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action mode")
    return normalized


DEFAULT_USER_ACTION_MODE = "user_sign"


def _user_sign_claim_response(*, machine: Machine, claimant_user_id: str, write_result) -> MachineRevenueClaimResponse:
    return MachineRevenueClaimResponse(
        machine_id=machine.id,
        onchain_machine_id=machine.onchain_machine_id,
        claimant_user_id=claimant_user_id,
        mode="user_sign",
        chain_id=write_result.chain_id,
        contract_address=write_result.contract_address,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
        submit_payload=write_result.payload,
        calldata=encode_contract_call(write_result),
    )


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _has_other_unsettled_revenue(machine_id: str, current_order_id: str, db: Session) -> bool:
    unsettled_count = db.scalar(
        select(func.count(Order.id)).where(
            Order.machine_id == machine_id,
            Order.id != current_order_id,
            Order.settlement_beneficiary_user_id.is_not(None),
            Order.settlement_state != SettlementState.DISTRIBUTED,
        )
    )
    return bool(unsettled_count)


def _machine_ids_for_owner(owner_user_id: str, db: Session) -> list[str]:
    return db.scalars(select(Machine.id).where(Machine.owner_user_id == owner_user_id)).all()


def _sum_machine_share_cents(*, machine_ids: list[str], db: Session) -> int:
    if not machine_ids:
        return 0
    return (
        db.scalar(
            select(func.coalesce(func.sum(RevenueEntry.machine_share_cents), 0)).where(
                RevenueEntry.machine_id.in_(machine_ids)
            )
        )
        or 0
    )


def _sum_machine_claims_cents(*, machine_ids: list[str], db: Session) -> int:
    if not machine_ids:
        return 0
    return (
        db.scalar(
            select(func.coalesce(func.sum(MachineRevenueClaim.amount_cents), 0)).where(
                MachineRevenueClaim.machine_id.in_(machine_ids)
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
    now = datetime.now(timezone.utc)

    order.settlement_state = SettlementState.DISTRIBUTED
    if settlement is None:
        settlement, entry = ensure_confirmed_settlement_projection(
            db=db,
            order=order,
            machine=machine,
            gross_amount_cents=effective_paid_cents,
            distributed_at=now,
        )
    else:
        settlement, entry = ensure_confirmed_settlement_projection(
            db=db,
            order=order,
            machine=machine,
            gross_amount_cents=settlement.gross_amount_cents,
            distributed_at=settlement.distributed_at or now,
        )
        machine.has_unsettled_revenue = _has_other_unsettled_revenue(machine.id, order.id, db)
        db.add(machine)

    db.add(order)
    db.commit()

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
        distributed_at=settlement.distributed_at or now,
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
    machine_ids = _machine_ids_for_owner(owner_user_id=owner_user_id, db=db)
    projected_cents = _sum_machine_share_cents(machine_ids=machine_ids, db=db)
    claimed_cents = _sum_machine_claims_cents(machine_ids=machine_ids, db=db)
    claimable_cents = max(0, projected_cents - claimed_cents)
    withdraw_history = (
        list(
            db.scalars(
                select(MachineRevenueClaim)
                .where(MachineRevenueClaim.machine_id.in_(machine_ids))
                .order_by(MachineRevenueClaim.claimed_at.desc())
            )
        )
        if machine_ids
        else []
    )
    return RevenueAccountOverviewResponse(
        owner_user_id=owner_user_id,
        paid_cents=_user_paid_cents(user_id=owner_user_id, db=db),
        projected_cents=projected_cents,
        claimable_cents=claimable_cents,
        claimed_cents=claimed_cents,
        currency=_user_primary_currency(user_id=owner_user_id, db=db),
        withdraw_history=withdraw_history,
    )


@router.post("/machines/{machine_id}/claim", response_model=MachineRevenueClaimResponse, response_model_exclude_none=True)
def claim_machine_revenue(
    machine_id: str,
    mode: str = Query(default=DEFAULT_USER_ACTION_MODE),
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> MachineRevenueClaimResponse:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")
    if not machine.onchain_machine_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Machine is not anchored onchain")
    if not machine.has_unsettled_revenue:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Machine has no unsettled revenue to claim",
        )

    claimable_cents = _machine_claimable_cents(machine_id=machine.id, db=db)

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.claim_machine_revenue(machine)
    if action_mode == "user_sign":
        return _user_sign_claim_response(
            machine=machine,
            claimant_user_id=machine.owner_user_id,
            write_result=write_result,
        )

    try:
        broadcast = onchain_lifecycle.send_as_user(
            user_id=machine.owner_user_id,
            write_result=write_result,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain machine-owner signer is not configured: {exc}",
        ) from exc

    if claimable_cents > 0:
        claim_record = MachineRevenueClaim(
            machine_id=machine.id,
            amount_cents=claimable_cents,
            tx_hash=broadcast.tx_hash,
        )
        db.add(claim_record)
        db.commit()

    return MachineRevenueClaimResponse(
        machine_id=machine.id,
        onchain_machine_id=machine.onchain_machine_id,
        claimant_user_id=machine.owner_user_id,
        tx_hash=broadcast.tx_hash,
        contract_name="RevenueVault",
        method_name="claimMachineRevenue",
    )
