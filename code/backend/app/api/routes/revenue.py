from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import PaymentState, SettlementState
from app.domain.models import Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.domain.rules import has_sufficient_payment
from app.schemas.revenue import RevenueDistributionResponse, RevenueEntryResponse

router = APIRouter()


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


@router.post("/orders/{order_id}/distribute", response_model=RevenueDistributionResponse)
def distribute_revenue(order_id: str, db: Session = Depends(get_db)) -> RevenueDistributionResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()
    if settlement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")
    if settlement.state != SettlementState.LOCKED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Settlement is not locked")

    paid_cents = _succeeded_payment_total_cents(order.id, db)
    if not has_sufficient_payment(order.quoted_amount_cents, paid_cents):
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

    entry = RevenueEntry(
        order_id=order.id,
        settlement_id=settlement.id,
        machine_id=machine.id,
        beneficiary_user_id=order.settlement_beneficiary_user_id,
        gross_amount_cents=settlement.gross_amount_cents,
        platform_fee_cents=settlement.platform_fee_cents,
        machine_share_cents=settlement.machine_share_cents,
        is_self_use=self_use,
        is_dividend_eligible=dividend_eligible,
    )
    settlement.state = SettlementState.DISTRIBUTED
    settlement.distributed_at = now
    order.settlement_state = SettlementState.DISTRIBUTED
    machine.has_unsettled_revenue = False

    db.add(entry)
    db.add(settlement)
    db.add(order)
    db.add(machine)
    db.commit()

    return RevenueDistributionResponse(
        order_id=order.id,
        settlement_id=settlement.id,
        machine_id=machine.id,
        beneficiary_user_id=order.settlement_beneficiary_user_id,
        gross_amount_cents=settlement.gross_amount_cents,
        platform_fee_cents=settlement.platform_fee_cents,
        machine_share_cents=settlement.machine_share_cents,
        is_self_use=self_use,
        is_dividend_eligible=dividend_eligible,
        distributed_at=now,
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
