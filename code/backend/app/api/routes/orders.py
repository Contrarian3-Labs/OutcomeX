from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import ExecutionRun, Machine, Order, Payment
from app.domain.planning import summarize_plan_from_chat
from app.domain.rules import has_sufficient_payment
from app.execution import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.integrations.onchain_broadcaster import OnchainBroadcaster, get_onchain_broadcaster
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.schemas.execution_run import ExecutionRunResponse
from app.schemas.order import OrderCreateRequest, OrderResponse, ResultConfirmResponse, ResultReadyResponse

router = APIRouter()


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _build_execution_plan(*, intent_id: str, prompt: str, input_files: list[str], execution_strategy: ExecutionStrategy):
    return ExecutionEngineService().plan(
        IntentRequest(
            intent_id=intent_id,
            prompt=prompt,
            input_files=tuple(input_files),
            execution_strategy=execution_strategy,
        )
    )


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
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
    plan = _build_execution_plan(
        intent_id=f"order-{order.id}",
        prompt=payload.user_prompt,
        input_files=payload.input_files,
        execution_strategy=payload.execution_strategy,
    )
    order.execution_request = plan.execution_request
    order.execution_metadata = plan.metadata
    create_order_write = order_writer.create_order(order)
    create_order_receipt = onchain_broadcaster.broadcast_create_order(
        order=order,
        write_result=create_order_write,
    )
    order.onchain_order_id = create_order_receipt.onchain_order_id
    order.create_order_tx_hash = create_order_receipt.tx_hash
    order.create_order_event_id = create_order_receipt.event_id
    order.create_order_block_number = create_order_receipt.block_number
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


@router.post("/{order_id}/start-execution", response_model=ExecutionRunResponse)
def start_order_execution(
    order_id: str,
    db: Session = Depends(get_db),
    execution_service=Depends(get_agentskillos_execution_service),
) -> ExecutionRun:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    paid_cents = _succeeded_payment_total_cents(order.id, db)
    if not has_sufficient_payment(order.quoted_amount_cents, paid_cents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order execution requires full payment",
        )

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    dispatch = ExecutionEngineService(execution_service=execution_service).dispatch(
        IntentRequest(
            intent_id=order.id,
            prompt=order.user_prompt,
            input_files=tuple((order.execution_request or {}).get("files") or ()),
            execution_strategy=ExecutionStrategy((order.execution_request or {}).get("execution_strategy", "quality")),
        )
    )
    if not dispatch.accepted or dispatch.run_id is None or dispatch.run_status is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")

    run_status = ExecutionRunStatus(dispatch.run_status.value)
    run = ExecutionRun(
        id=dispatch.run_id,
        order_id=order.id,
        external_order_id=order.id,
        status=run_status,
        submission_payload=order.execution_request,
        workspace_path=None,
        run_dir=None,
        preview_manifest=[],
        artifact_manifest=[],
        skills_manifest=[],
        model_usage_manifest=[],
        summary_metrics={},
        error=None,
        started_at=None,
        finished_at=None,
    )
    run = db.merge(run)

    order.state = OrderState.EXECUTING
    order.execution_state = ExecutionState.RUNNING if run_status == ExecutionRunStatus.RUNNING else ExecutionState.QUEUED
    order.preview_state = PreviewState.GENERATING
    metadata = dict(order.execution_metadata or {})
    metadata["run_id"] = dispatch.run_id
    metadata["run_status"] = run_status.value
    order.execution_metadata = metadata
    machine.has_active_tasks = True
    db.add(order)
    db.add(machine)
    db.commit()
    db.refresh(run)
    return run
