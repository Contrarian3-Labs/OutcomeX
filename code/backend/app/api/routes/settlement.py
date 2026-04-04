from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import PaymentState, SettlementState
from app.domain.models import Machine, Order, Payment, SettlementRecord
from app.domain.rules import calculate_revenue_split, can_start_settlement, has_sufficient_payment
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.schemas.settlement import SettlementPreviewResponse, SettlementStartResponse

router = APIRouter()


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _validated_order_for_settlement(order_id: str, db: Session) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not can_start_settlement(order.state, order.result_confirmed_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement can only start after result confirmation",
        )
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    if not has_sufficient_payment(order.quoted_amount_cents, paid_cents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement requires full successful payment",
        )
    if (
        order.settlement_beneficiary_user_id is None
        or order.settlement_is_self_use is None
        or order.settlement_is_dividend_eligible is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement policy must be frozen before settlement",
        )
    return order


@router.post("/orders/{order_id}/preview", response_model=SettlementPreviewResponse)
def preview_settlement(order_id: str, db: Session = Depends(get_db)) -> SettlementPreviewResponse:
    order = _validated_order_for_settlement(order_id, db)
    platform_fee_cents, machine_share_cents = calculate_revenue_split(order.quoted_amount_cents)
    return SettlementPreviewResponse(
        order_id=order.id,
        gross_amount_cents=order.quoted_amount_cents,
        platform_fee_cents=platform_fee_cents,
        machine_share_cents=machine_share_cents,
        state=SettlementState.READY,
    )


@router.post("/orders/{order_id}/start", response_model=SettlementStartResponse)
def start_settlement(
    order_id: str,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> SettlementStartResponse:
    order = _validated_order_for_settlement(order_id, db)
    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    existing = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()
    if existing is not None:
        if existing.state != SettlementState.DISTRIBUTED and not machine.has_unsettled_revenue:
            machine.has_unsettled_revenue = True
            db.add(machine)
            db.commit()
        return SettlementStartResponse(
            settlement_id=existing.id,
            order_id=existing.order_id,
            state=existing.state,
            created_at=existing.created_at,
        )

    platform_fee_cents, machine_share_cents = calculate_revenue_split(order.quoted_amount_cents)
    settlement = SettlementRecord(
        order_id=order.id,
        gross_amount_cents=order.quoted_amount_cents,
        platform_fee_cents=platform_fee_cents,
        machine_share_cents=machine_share_cents,
        state=SettlementState.LOCKED,
    )
    order.settlement_state = SettlementState.LOCKED
    machine.has_unsettled_revenue = True

    db.add(settlement)
    db.add(order)
    db.add(machine)
    db.flush()
    order_writer.settle_order(order, settlement)
    db.commit()
    db.refresh(settlement)
    return SettlementStartResponse(
        settlement_id=settlement.id,
        order_id=settlement.order_id,
        state=settlement.state,
        created_at=settlement.created_at,
    )
