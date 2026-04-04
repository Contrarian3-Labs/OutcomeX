from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, Order, Payment
from app.domain.planning import summarize_plan_from_chat
from app.domain.rules import has_sufficient_payment
from app.execution import IntentRequest
from app.execution.service import ExecutionEngineService
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.schemas.order import OrderCreateRequest, OrderResponse, ResultConfirmResponse, ResultReadyResponse

router = APIRouter()


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _build_execution_metadata(*, intent_id: str, prompt: str) -> dict[str, str]:
    plan = ExecutionEngineService().plan(IntentRequest(intent_id=intent_id, prompt=prompt))
    metadata = dict(plan.metadata)
    metadata.setdefault("requested_outputs", plan.recipe.metadata.get("requested_outputs", ""))
    metadata.setdefault("primary_output", plan.recipe.metadata.get("primary_output", ""))
    metadata.setdefault("match_status", plan.match.status.value)
    if plan.match.selected is not None:
        metadata.setdefault("selected_provider", plan.match.selected.provider)
        metadata.setdefault("selected_model", plan.match.selected.model_id)
    else:
        metadata.setdefault("selected_provider", "")
        metadata.setdefault("selected_model", "")
    return metadata


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> Order:
    machine = db.get(Machine, payload.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    order = Order(
        user_id=payload.user_id,
        machine_id=machine.id,
        chat_session_id=payload.chat_session_id,
        user_prompt=payload.user_prompt,
        recommended_plan_summary=summarize_plan_from_chat(payload.user_prompt),
        quoted_amount_cents=payload.quoted_amount_cents,
        state=OrderState.PLAN_RECOMMENDED,
    )
    db.add(order)
    db.flush()
    order.execution_metadata = _build_execution_metadata(
        intent_id=f"order-{order.id}",
        prompt=payload.user_prompt,
    )
    order_writer.create_order(order)
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
def confirm_order_result(
    order_id: str,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> ResultConfirmResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    paid_cents = _succeeded_payment_total_cents(order.id, db)
    if not has_sufficient_payment(order.quoted_amount_cents, paid_cents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order cannot be confirmed before full payment",
        )

    if order.execution_state != ExecutionState.SUCCEEDED or order.preview_state != PreviewState.READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order result is not ready for confirmation",
        )

    if (
        order.settlement_beneficiary_user_id is None
        or order.settlement_is_self_use is None
        or order.settlement_is_dividend_eligible is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settlement policy must be frozen after payment",
        )

    confirmed_at = datetime.now(timezone.utc)
    order.state = OrderState.RESULT_CONFIRMED
    order.result_confirmed_at = confirmed_at
    order.settlement_state = SettlementState.READY
    db.add(order)
    db.flush()
    order_writer.confirm_result(order)
    db.commit()

    return ResultConfirmResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        result_confirmed_at=confirmed_at,
    )


@router.post("/{order_id}/mock-result-ready", response_model=ResultReadyResponse)
def mock_mark_result_ready(
    order_id: str,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> ResultReadyResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order.state != OrderState.RESULT_CONFIRMED:
        order.state = OrderState.RESULT_PENDING_CONFIRMATION
    order.execution_state = ExecutionState.SUCCEEDED
    order.preview_state = PreviewState.READY
    db.add(order)
    db.flush()
    order_writer.mark_preview_ready(order)
    db.commit()

    return ResultReadyResponse(
        order_id=order.id,
        state=order.state,
        execution_state=order.execution_state,
        preview_state=order.preview_state,
    )
