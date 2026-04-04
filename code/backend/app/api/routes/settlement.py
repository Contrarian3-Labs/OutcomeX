from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import SettlementState
from app.domain.models import Order, SettlementRecord
from app.domain.rules import calculate_revenue_split, can_start_settlement
from app.schemas.settlement import SettlementPreviewResponse, SettlementStartResponse

router = APIRouter()


def _validated_order_for_settlement(order_id: str, db: Session) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not can_start_settlement(order.state, order.result_confirmed_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement can only start after result confirmation",
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
def start_settlement(order_id: str, db: Session = Depends(get_db)) -> SettlementStartResponse:
    order = _validated_order_for_settlement(order_id, db)

    existing = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()
    if existing is not None:
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

    db.add(settlement)
    db.add(order)
    db.commit()
    db.refresh(settlement)
    return SettlementStartResponse(
        settlement_id=settlement.id,
        order_id=settlement.order_id,
        state=settlement.state,
        created_at=settlement.created_at,
    )

