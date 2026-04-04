from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import OrderState, SettlementState
from app.domain.models import Order
from app.domain.planning import summarize_plan_from_chat
from app.schemas.order import OrderCreateRequest, OrderResponse, ResultConfirmResponse

router = APIRouter()


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreateRequest, db: Session = Depends(get_db)) -> Order:
    order = Order(
        user_id=payload.user_id,
        machine_id=payload.machine_id,
        chat_session_id=payload.chat_session_id,
        user_prompt=payload.user_prompt,
        recommended_plan_summary=summarize_plan_from_chat(payload.user_prompt),
        quoted_amount_cents=payload.quoted_amount_cents,
        state=OrderState.PLAN_RECOMMENDED,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.post("/{order_id}/confirm-result", response_model=ResultConfirmResponse)
def confirm_order_result(order_id: str, db: Session = Depends(get_db)) -> ResultConfirmResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    confirmed_at = datetime.now(timezone.utc)
    order.state = OrderState.RESULT_CONFIRMED
    order.result_confirmed_at = confirmed_at
    order.settlement_state = SettlementState.READY
    db.add(order)
    db.commit()

    return ResultConfirmResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        result_confirmed_at=confirmed_at,
    )

