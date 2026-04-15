from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.execution_contract import (
    build_selected_plan_binding,
    build_selected_plan_payload,
    is_order_execution_contract_consistent,
)
from app.core.container import get_container
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.benchmark_solutions import BenchmarkSolution, get_benchmark_solution
from app.domain.claim_projection import project_order_refund_claim
from app.domain.enums import ExecutionRunStatus, ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import ExecutionRun, Machine, Order, Payment, utc_now
from app.domain.order_truth import set_authoritative_order_truth
from app.domain.planning import build_fast_recommended_plans, select_recommended_plan
from app.domain.pwr_amounts import pwr_wei_to_float
from app.domain.revenue_amounts import latest_success_payment
from app.domain.rules import (
    calculate_failed_or_no_valid_preview_breakdown,
    calculate_rejected_valid_preview_breakdown,
    has_sufficient_payment,
)
from app.domain.settlement_projection import ensure_confirmed_settlement_projection, ensure_settlement_projection
from app.execution import ExecutionStrategy, IntentRequest
from app.execution.service import ExecutionEngineService
from app.integrations.agentskillos_execution_service import get_agentskillos_execution_service
from app.onchain.claim_state_reader import SettlementClaimStateReader, get_settlement_claim_state_reader
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.manual_projection_sync import sync_projection_from_tx_hash
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import encode_contract_call
from app.runtime.cost_service import get_runtime_cost_service
from app.runtime.hardware_simulator import get_shared_hardware_simulator
from app.schemas.execution_run import ExecutionRunResponse
from app.schemas.order import (
    OrderActionSyncRequest,
    OrderActionSyncResponse,
    OrderAvailableActionsResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
    OrderSettlementActionResponse,
    ResultReadyRequest,
    ResultReadyResponse,
)
from app.schemas.settlement import RefundClaimResponse
from app.services.attachments import (
    AttachmentResolutionError,
    build_planning_context_id,
    resolve_planning_input_files,
    stage_bound_execution_input_files,
)

router = APIRouter()


def build_recommended_plans(  # noqa: PLR0913
    *,
    user_id: str,
    chat_session_id: str,
    user_message: str,
    preferred_strategy: ExecutionStrategy | None,
    input_files: tuple[str, ...],
    planning_context_key: str = "",
):
    input_files
    return build_fast_recommended_plans(
        user_id=user_id,
        chat_session_id=chat_session_id,
        user_message=user_message,
        preferred_strategy=preferred_strategy,
        planning_context_key=planning_context_key,
    )


def _resolve_order_execution_inputs(payload: OrderCreateRequest) -> tuple[str, list[str], BenchmarkSolution | None]:
    if not payload.benchmark_task_id:
        return payload.user_prompt, payload.input_files, None
    solution = get_benchmark_solution(payload.benchmark_task_id)
    if solution is None:
        return payload.user_prompt, payload.input_files, None
    return solution.benchmark_prompt, list(solution.input_files), solution


def _prefer_native_plan_index(plans: tuple, *, native_plan_index: int | None):
    if native_plan_index is None:
        return plans
    preferred = [plan for plan in plans if plan.native_plan_index == native_plan_index]
    if not preferred:
        return plans
    remaining = [plan for plan in plans if plan.native_plan_index != native_plan_index]
    return tuple(preferred + remaining)


def _preview_valid(order: Order) -> bool | None:
    metadata = dict(order.execution_metadata or {})
    preview_valid = metadata.get("preview_valid")
    if preview_valid is None:
        return True if order.preview_state == PreviewState.READY and order.execution_state == ExecutionState.SUCCEEDED else None
    return bool(preview_valid)


def _ensure_demo_write_allowed() -> None:
    if get_settings().env not in {"dev", "test"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mock result-ready is only available in dev/test",
        )


def _normalize_action_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in {"server_broadcast", "user_sign"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action mode")
    return normalized


def _is_settlement_policy_frozen(order: Order) -> bool:
    return (
        order.settlement_beneficiary_user_id is not None
        and order.settlement_is_self_use is not None
        and order.settlement_is_dividend_eligible is not None
    )


def _can_confirm_result(order: Order, db: Session) -> bool:
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    return (
        has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents)
        and order.execution_state == ExecutionState.SUCCEEDED
        and order.preview_state == PreviewState.READY
        and _preview_valid(order) is not False
        and _is_settlement_policy_frozen(order)
        and order.settlement_state not in {SettlementState.LOCKED, SettlementState.DISTRIBUTED}
    )


def _can_reject_valid_preview(order: Order, db: Session) -> bool:
    return _can_confirm_result(order, db) and _preview_valid(order) is True


def _can_refund_failed_or_no_valid_preview(order: Order, db: Session) -> bool:
    if order.settlement_state in {SettlementState.LOCKED, SettlementState.DISTRIBUTED}:
        return False
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    if not has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents):
        return False
    preview_valid = _preview_valid(order)
    return bool(
        _is_settlement_policy_frozen(order)
        and (
            (order.preview_state == PreviewState.READY and preview_valid is False)
            or order.execution_state in {ExecutionState.FAILED, ExecutionState.CANCELLED}
        )
    )

def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _authoritative_event_id_from_broadcast(broadcast) -> str:
    receipt = getattr(broadcast, "receipt", None)
    receipt_event_id = getattr(receipt, "event_id", None)
    if receipt_event_id:
        return str(receipt_event_id)
    return f"tx:{broadcast.tx_hash}"


def _project_authoritative_order_status(
    *,
    order: Order,
    order_status: str,
    event_id: str,
    db: Session,
) -> None:
    set_authoritative_order_truth(order, order_status=order_status, event_id=event_id)
    machine = db.get(Machine, order.machine_id)

    if order_status == "PREVIEW_READY":
        order.preview_state = PreviewState.READY
        if order.state == OrderState.EXECUTING:
            order.state = OrderState.RESULT_PENDING_CONFIRMATION
        db.add(order)
        if machine is not None:
            db.add(machine)
        return

    if order_status in {"CONFIRMED", "REJECTED", "REFUNDED"}:
        if machine is not None:
            machine.has_active_tasks = False
            db.add(machine)

        paid_cents = _succeeded_payment_total_cents(order.id, db)
        gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)

        if order_status == "CONFIRMED":
            order.state = OrderState.RESULT_CONFIRMED
            order.result_confirmed_at = order.result_confirmed_at or utc_now()
            if machine is not None:
                settlement, entry = ensure_confirmed_settlement_projection(
                    db=db,
                    order=order,
                    machine=machine,
                    gross_amount_cents=gross_amount_cents,
                    distributed_at=order.result_confirmed_at,
                )
                settlement.state = SettlementState.DISTRIBUTED
                db.add(settlement)
                db.add(entry)
        else:
            order.state = OrderState.CANCELLED
            breakdown = (
                calculate_rejected_valid_preview_breakdown(gross_amount_cents)
                if order_status == "REJECTED"
                else calculate_failed_or_no_valid_preview_breakdown(gross_amount_cents)
            )
            if machine is not None:
                settlement, entry = ensure_settlement_projection(
                    db=db,
                    order=order,
                    machine=machine,
                    gross_amount_cents=breakdown.gross_amount_cents,
                    platform_fee_cents=breakdown.platform_fee_cents,
                    machine_share_cents=breakdown.machine_share_cents,
                    distributed_at=utc_now(),
                )
                settlement.state = SettlementState.DISTRIBUTED
                db.add(settlement)
                db.add(entry)

        order.settlement_state = SettlementState.DISTRIBUTED
        db.add(order)


def _has_authoritative_paid_projection(order: Order) -> bool:
    metadata = dict(order.execution_metadata or {})
    return metadata.get("authoritative_paid_projection") is True


def _estimated_order_workload(order: Order, machine_id: str):
    execution_request = dict(order.execution_request or {})
    return ExecutionEngineService._estimate_workload(
        IntentRequest(
            intent_id=order.id,
            prompt=order.user_prompt,
            input_files=tuple(execution_request.get("files") or ()),
            execution_strategy=ExecutionStrategy(execution_request.get("execution_strategy", "quality")),
            context={"machine_id": machine_id},
        )
    )


def _machine_is_runtime_available(order: Order, machine_id: str) -> bool:
    simulator = get_shared_hardware_simulator(machine_id)
    snapshot = get_shared_hardware_simulator(machine_id).snapshot()
    workload = _estimated_order_workload(order, machine_id)
    can_run_now = (
        snapshot.running_count < snapshot.max_concurrency
        and snapshot.used_capacity_units + workload.capacity_units <= snapshot.total_capacity_units
        and snapshot.used_memory_mb + workload.memory_mb <= snapshot.total_memory_mb
    )
    if can_run_now:
        return True
    return snapshot.queued_count < simulator.profile.max_queue_depth


def _request_workload(
    *,
    prompt: str,
    input_files: list[str],
    execution_strategy: ExecutionStrategy,
    machine_id: str,
):
    return ExecutionEngineService._estimate_workload(
        IntentRequest(
            intent_id=f"route:{machine_id}",
            prompt=prompt,
            input_files=tuple(input_files),
            execution_strategy=execution_strategy,
            context={"machine_id": machine_id},
        )
    )


def _machine_can_accept_order_request(
    *,
    machine: Machine,
    prompt: str,
    input_files: list[str],
    execution_strategy: ExecutionStrategy,
) -> bool:
    if machine.has_active_tasks or machine.has_unsettled_revenue:
        return False

    simulator = get_shared_hardware_simulator(machine.id)
    snapshot = simulator.snapshot()
    workload = _request_workload(
        prompt=prompt,
        input_files=input_files,
        execution_strategy=execution_strategy,
        machine_id=machine.id,
    )
    can_run_now = (
        snapshot.running_count < snapshot.max_concurrency
        and snapshot.used_capacity_units + workload.capacity_units <= snapshot.total_capacity_units
        and snapshot.used_memory_mb + workload.memory_mb <= snapshot.total_memory_mb
    )
    if can_run_now:
        return True
    return snapshot.queued_count < simulator.profile.max_queue_depth


def _route_order_machine(
    *,
    payload: OrderCreateRequest,
    db: Session,
    prompt: str,
    input_files: list[str],
    execution_strategy: ExecutionStrategy,
) -> Machine:
    requested_machine = db.get(Machine, payload.machine_id) if payload.machine_id else None
    if payload.machine_id and requested_machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if requested_machine is not None:
        if requested_machine.owner_user_id == payload.user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Create order cannot target your own machine. Use the self-use workspace instead.",
            )
        if not _machine_can_accept_order_request(
            machine=requested_machine,
            prompt=prompt,
            input_files=input_files,
            execution_strategy=execution_strategy,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Requested machine is not currently available for routed execution.",
            )
        return requested_machine

    candidates = list(
        db.scalars(
            select(Machine)
            .where(Machine.owner_user_id != payload.user_id)
            .order_by(Machine.created_at.asc(), Machine.id.asc())
        )
    )
    for machine in candidates:
        if _machine_can_accept_order_request(
            machine=machine,
            prompt=prompt,
            input_files=input_files,
            execution_strategy=execution_strategy,
        ):
            return machine

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="No active non-owner machine is currently available for routed execution.",
    )


def _serialize_order(order: Order, *, db: Session) -> OrderResponse:
    machine = db.get(Machine, order.machine_id) if order.machine_id else None
    machine_is_available = _machine_is_runtime_available(order, machine.id) if machine is not None else None
    quote_snapshot = _resolve_order_quote_snapshot(order)
    latest_payment = max(order.payments, key=lambda payment: payment.created_at) if order.payments else None
    return OrderResponse.model_validate(
        {
            "id": order.id,
            "onchain_order_id": order.onchain_order_id,
            "onchain_machine_id": order.onchain_machine_id,
            "create_order_tx_hash": order.create_order_tx_hash,
            "create_order_event_id": order.create_order_event_id,
            "create_order_block_number": order.create_order_block_number,
            "user_id": order.user_id,
            "machine_id": order.machine_id,
            "chat_session_id": order.chat_session_id,
            "user_prompt": order.user_prompt,
            "recommended_plan_summary": order.recommended_plan_summary,
            "quoted_amount_cents": order.quoted_amount_cents,
            "quoted_pwr_amount": quote_snapshot["pwr_quote"],
            "quoted_pwr_anchor_price_cents": quote_snapshot["pwr_anchor_price_cents"],
            "quoted_pricing_version": quote_snapshot["pricing_version"],
            "payment_state": order.payment_state,
            "unpaid_expiry_at": order.unpaid_expiry_at,
            "cancelled_at": order.cancelled_at,
            "is_expired": order.is_expired,
            "is_cancelled": order.is_cancelled,
            "machine_is_available": machine_is_available,
            "state": order.state,
            "execution_state": order.execution_state,
            "preview_state": order.preview_state,
            "settlement_state": order.settlement_state,
            "settlement_beneficiary_user_id": order.settlement_beneficiary_user_id,
            "settlement_is_self_use": order.settlement_is_self_use,
            "settlement_is_dividend_eligible": order.settlement_is_dividend_eligible,
            "execution_request": order.execution_request,
            "execution_metadata": order.execution_metadata,
            "latest_success_payment_currency": order.latest_success_payment_currency,
            "latest_payment": (
                {
                    "payment_id": latest_payment.id,
                    "provider": latest_payment.provider,
                    "provider_reference": latest_payment.provider_reference,
                    "merchant_order_id": latest_payment.merchant_order_id,
                    "checkout_url": latest_payment.checkout_url,
                    "state": latest_payment.state,
                    "callback_state": latest_payment.callback_state,
                    "callback_event_id": latest_payment.callback_event_id,
                    "callback_tx_hash": latest_payment.callback_tx_hash,
                    "amount_cents": latest_payment.amount_cents,
                    "currency": latest_payment.currency,
                    "created_at": latest_payment.created_at,
                }
                if latest_payment is not None
                else None
            ),
            "result_confirmed_at": order.result_confirmed_at,
            "created_at": order.created_at,
        }
    )


def _resolve_order_quote_snapshot(order: Order) -> dict[str, int | str | None]:
    metadata = dict(order.execution_metadata or {})
    raw_pwr_quote = metadata.get("quoted_pwr_amount")
    raw_anchor_price = metadata.get("quoted_pwr_anchor_price_cents")
    raw_pricing_version = metadata.get("quoted_pricing_version")
    if (
        isinstance(raw_pwr_quote, str)
        and raw_pwr_quote.strip()
        and isinstance(raw_anchor_price, int)
        and raw_anchor_price > 0
        and isinstance(raw_pricing_version, str)
        and raw_pricing_version.strip()
    ):
        return {
            "pwr_quote": raw_pwr_quote,
            "pwr_anchor_price_cents": raw_anchor_price,
            "pricing_version": raw_pricing_version,
        }

    quote = get_runtime_cost_service().quote_for_order_amount(order.quoted_amount_cents)
    return {
        "pwr_quote": quote.pwr_quote,
        "pwr_anchor_price_cents": quote.pwr_anchor_price_cents,
        "pricing_version": quote.pricing_version,
    }


def _has_active_execution_run(order_id: str, db: Session) -> bool:
    active_count = db.scalar(
        select(func.count(ExecutionRun.id)).where(
            ExecutionRun.order_id == order_id,
            ExecutionRun.status.in_(
                (
                    ExecutionRunStatus.QUEUED,
                    ExecutionRunStatus.PLANNING,
                    ExecutionRunStatus.RUNNING,
                )
            ),
        )
    )
    return bool(active_count)


def _build_execution_plan(
    *,
    intent_id: str,
    prompt: str,
    input_files: list[str],
    execution_strategy: ExecutionStrategy,
    machine_id: str | None = None,
    selected_native_plan_index: int | None = None,
):
    context = {}
    if machine_id is not None:
        context["machine_id"] = machine_id
    if selected_native_plan_index is not None:
        context["selected_native_plan_index"] = str(selected_native_plan_index)
    return ExecutionEngineService().plan(
        IntentRequest(
            intent_id=intent_id,
            prompt=prompt,
            input_files=tuple(input_files),
            execution_strategy=execution_strategy,
            context=context,
        )
    )


def _decode_orders_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created_at_raw, order_id = cursor.split("|", 1)
        if not order_id:
            raise ValueError("missing order id in cursor")
        created_at = datetime.fromisoformat(created_at_raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from exc
    return created_at, order_id


def _encode_orders_cursor(order: Order) -> str:
    return f"{order.created_at.isoformat()}|{order.id}"


def _user_sign_refund_claim_response(*, order: Order, currency: str, write_result) -> RefundClaimResponse:
    return RefundClaimResponse(
        order_id=order.id,
        claimant_user_id=order.user_id,
        currency=currency.upper(),
        mode="user_sign",
        chain_id=write_result.chain_id,
        contract_address=write_result.contract_address,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
        submit_payload=write_result.payload,
        calldata=encode_contract_call(write_result),
    )


def _user_sign_order_action_response(*, order: Order, write_result) -> OrderSettlementActionResponse:
    return OrderSettlementActionResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        mode="user_sign",
        tx_hash=None,
        chain_id=write_result.chain_id,
        contract_address=write_result.contract_address,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
        submit_payload=write_result.payload,
        calldata=encode_contract_call(write_result),
    )


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
) -> OrderResponse:
    payload = payload.model_copy(
        update={"user_id": get_container().buyer_address_resolver.canonicalize_user_id(payload.user_id)},
    )
    execution_prompt, execution_input_files, benchmark_solution = _resolve_order_execution_inputs(payload)

    derived_planning_context_id = build_planning_context_id(
        input_files=tuple(execution_input_files),
        attachment_session_id=payload.attachment_session_id,
        attachment_ids=tuple(payload.attachment_ids),
    )
    if (
        payload.planning_context_id
        and (execution_input_files or payload.attachment_session_id or payload.attachment_ids)
        and payload.planning_context_id != derived_planning_context_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="planning_context_id does not match input_files/attachment references",
        )
    planning_context_id = payload.planning_context_id or derived_planning_context_id
    try:
        with resolve_planning_input_files(
            db=db,
            input_files=tuple(execution_input_files),
            attachment_session_id=payload.attachment_session_id,
            attachment_session_token=payload.attachment_session_token,
            attachment_ids=tuple(payload.attachment_ids),
        ) as resolved_planning_input_files:
            recommended_plans = build_recommended_plans(
                user_id=payload.user_id,
                chat_session_id=payload.chat_session_id,
                user_message=execution_prompt,
                preferred_strategy=(
                    benchmark_solution.preferred_execution_strategy
                    if benchmark_solution and payload.selected_plan_id is None
                    else payload.execution_strategy
                ),
                input_files=resolved_planning_input_files,
                planning_context_key=planning_context_id,
            )
    except AttachmentResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    recommended_plans = _prefer_native_plan_index(
        recommended_plans,
        native_plan_index=(
            benchmark_solution.preferred_native_plan_index
            if benchmark_solution and payload.selected_plan_id is None
            else None
        ),
    )
    selected_plan = None
    if benchmark_solution and payload.selected_plan_id is None and benchmark_solution.preferred_native_plan_index is not None:
        for plan in recommended_plans:
            if plan.native_plan_index == benchmark_solution.preferred_native_plan_index:
                selected_plan = plan
                break
    if selected_plan is None:
        selected_plan = select_recommended_plan(
            recommended_plans,
            selected_plan_id=payload.selected_plan_id,
            execution_strategy=payload.execution_strategy,
        )
    if selected_plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected plan is invalid for this request",
        )

    machine = _route_order_machine(
        payload=payload,
        db=db,
        prompt=execution_prompt,
        input_files=execution_input_files,
        execution_strategy=selected_plan.strategy,
    )

    order = Order(
        user_id=payload.user_id,
        machine_id=machine.id,
        onchain_machine_id=machine.onchain_machine_id,
        chat_session_id=payload.chat_session_id,
        user_prompt=payload.user_prompt,
        recommended_plan_summary=selected_plan.summary,
        quoted_amount_cents=payload.quoted_amount_cents,
        state=OrderState.PLAN_RECOMMENDED,
    )
    db.add(order)
    db.flush()
    execution_plan = _build_execution_plan(
        intent_id=f"order-{order.id}",
        prompt=execution_prompt,
        input_files=execution_input_files,
        execution_strategy=selected_plan.strategy,
        machine_id=machine.id,
        selected_native_plan_index=selected_plan.native_plan_index,
    )
    execution_metadata = dict(execution_plan.metadata)
    execution_request = dict(execution_plan.execution_request)
    execution_request["planning_context_id"] = planning_context_id
    execution_request["attachment_session_id"] = payload.attachment_session_id
    execution_request["attachment_ids"] = list(payload.attachment_ids)
    order.execution_request = execution_request
    execution_metadata["selected_plan_id"] = selected_plan.plan_id
    execution_metadata["selected_plan_title"] = selected_plan.title
    execution_metadata["selected_plan_strategy"] = selected_plan.strategy.value
    execution_metadata["selected_native_plan_index"] = selected_plan.native_plan_index
    if payload.benchmark_task_id:
        execution_metadata["benchmark_task_id"] = payload.benchmark_task_id
    if benchmark_solution:
        execution_metadata["benchmark_solution_title"] = benchmark_solution.title
    execution_metadata["planning_context_id"] = planning_context_id
    execution_metadata["attachment_session_id"] = payload.attachment_session_id
    execution_metadata["attachment_ids"] = list(payload.attachment_ids)
    quote_snapshot = get_runtime_cost_service().quote_for_order_amount(payload.quoted_amount_cents)
    execution_metadata["quoted_pwr_amount"] = quote_snapshot.pwr_quote
    execution_metadata["quoted_pwr_anchor_price_cents"] = quote_snapshot.pwr_anchor_price_cents
    execution_metadata["quoted_pricing_version"] = quote_snapshot.pricing_version
    if selected_plan.native_plan_name:
        execution_metadata["selected_native_plan_name"] = selected_plan.native_plan_name
    if selected_plan.native_plan_description:
        execution_metadata["selected_native_plan_description"] = selected_plan.native_plan_description
    if selected_plan.native_skill_ids:
        execution_metadata["selected_native_skill_ids"] = list(selected_plan.native_skill_ids)
    if selected_plan.native_plan_nodes:
        execution_metadata["selected_native_plan_nodes"] = [dict(node) for node in selected_plan.native_plan_nodes]
    order.execution_metadata = execution_metadata
    db.add(order)
    db.commit()
    db.refresh(order)
    return _serialize_order(order, db=db)


@router.get("", response_model=OrderListResponse)
def list_orders(
    user_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    state: OrderState | None = Query(default=None),
    db: Session = Depends(get_db),
) -> OrderListResponse:
    canonical_user_id = get_container().buyer_address_resolver.canonicalize_user_id(user_id)
    statement = select(Order).where(Order.user_id == canonical_user_id)

    if state is not None:
        statement = statement.where(Order.state == state)

    if cursor:
        cursor_created_at, cursor_order_id = _decode_orders_cursor(cursor)
        statement = statement.where(
            or_(
                Order.created_at < cursor_created_at,
                and_(Order.created_at == cursor_created_at, Order.id < cursor_order_id),
            )
        )

    orders = list(
        db.scalars(
            statement.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1),
        )
    )
    has_more = len(orders) > limit
    items = orders[:limit]
    next_cursor = _encode_orders_cursor(items[-1]) if has_more and items else None
    return OrderListResponse(items=[_serialize_order(order, db=db) for order in items], next_cursor=next_cursor)


@router.get("/{order_id}/available-actions", response_model=OrderAvailableActionsResponse)
def get_order_available_actions(
    order_id: str,
    db: Session = Depends(get_db),
) -> OrderAvailableActionsResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    refund_projection = project_order_refund_claim(order=order, db=db)
    refund_claim_currency = refund_projection.currency
    refund_claim_amount_cents = refund_projection.claimable_cents if refund_claim_currency is not None else None
    refund_claim_amount_pwr = (
        pwr_wei_to_float(refund_projection.claimable_amount_wei)
        if refund_claim_currency == "PWR"
        else None
    )
    can_claim_refund = (
        refund_claim_amount_pwr is not None and refund_claim_amount_pwr > 0
        if refund_claim_currency == "PWR"
        else refund_claim_amount_cents is not None and refund_claim_amount_cents > 0
    )
    return OrderAvailableActionsResponse(
        order_id=order.id,
        preview_valid=_preview_valid(order),
        can_confirm_result=_can_confirm_result(order, db),
        can_reject_valid_preview=_can_reject_valid_preview(order, db),
        can_refund_failed_or_no_valid_preview=_can_refund_failed_or_no_valid_preview(order, db),
        can_claim_refund=can_claim_refund,
        refund_claim_currency=refund_claim_currency,
        refund_claim_amount_cents=refund_claim_amount_cents,
        refund_claim_amount_pwr=refund_claim_amount_pwr,
        refund_claim_pwr_anchor_price_cents=refund_projection.pwr_anchor_price_cents,
    )


@router.post("/{order_id}/claim-refund", response_model=RefundClaimResponse, response_model_exclude_none=True)
def claim_order_refund(
    order_id: str,
    mode: str = Query(default="server_broadcast"),
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    claim_state_reader: SettlementClaimStateReader = Depends(get_settlement_claim_state_reader),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> RefundClaimResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")

    refund_projection = project_order_refund_claim(order=order, db=db)
    latest_payment = latest_success_payment(order_id=order.id, db=db)
    currency = (
        refund_projection.currency
        or order.latest_success_payment_currency
        or (latest_payment.currency.upper() if latest_payment is not None else None)
    )
    if currency is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Refund currency is unresolved")

    try:
        onchain_claimable = claim_state_reader.refundable_amount(user_id=order.user_id, currency=currency)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unable to read onchain refundable balance: {exc}",
        ) from exc
    if onchain_claimable <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order has no claimable onchain refund")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.claim_refund(currency=currency, user_id=order.user_id, order_id=order.id)
    if action_mode == "user_sign":
        return _user_sign_refund_claim_response(order=order, currency=currency, write_result=write_result)

    try:
        broadcast = onchain_lifecycle.send_as_user(user_id=order.user_id, write_result=write_result)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain refund claimant signer is not configured: {exc}",
        ) from exc

    return RefundClaimResponse(
        order_id=order.id,
        claimant_user_id=order.user_id,
        currency=currency.upper(),
        tx_hash=broadcast.tx_hash,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
    )


@router.post("/{order_id}/sync-onchain", response_model=OrderActionSyncResponse)
def sync_order_projection_from_tx_hash(
    order_id: str,
    payload: OrderActionSyncRequest,
    db: Session = Depends(get_db),
) -> OrderActionSyncResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    container = get_container()
    result = sync_projection_from_tx_hash(
        tx_hash=payload.tx_hash,
        session_factory=container.session_factory,
        owner_resolver=container.buyer_address_resolver.resolve_user_id,
        settings=container.settings,
    )

    db.expire_all()
    refreshed_order = db.get(Order, order_id)
    if refreshed_order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found after sync")

    return OrderActionSyncResponse(
        order_id=refreshed_order.id,
        tx_hash=result.tx_hash,
        receipt_found=result.receipt_found,
        applied_events=result.applied_events,
        event_names=list(result.event_names),
        state=refreshed_order.state,
        settlement_state=refreshed_order.settlement_state,
        execution_state=refreshed_order.execution_state,
        preview_state=refreshed_order.preview_state,
    )


@router.post("/{order_id}/confirm-result", response_model=OrderSettlementActionResponse, response_model_exclude_none=True)
def confirm_result(
    order_id: str,
    mode: str = Query(default="server_broadcast"),
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> OrderSettlementActionResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not _can_confirm_result(order, db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order cannot confirm result")
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.confirm_result(order)
    if action_mode == "user_sign":
        return _user_sign_order_action_response(order=order, write_result=write_result)

    try:
        broadcast = onchain_lifecycle.send_as_user(user_id=order.user_id, write_result=write_result)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain buyer signer is not configured: {exc}",
        ) from exc

    _project_authoritative_order_status(
        order=order,
        order_status="CONFIRMED",
        event_id=_authoritative_event_id_from_broadcast(broadcast),
        db=db,
    )
    db.commit()
    db.refresh(order)

    return OrderSettlementActionResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        tx_hash=broadcast.tx_hash,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
    )


@router.post(
    "/{order_id}/reject-valid-preview",
    response_model=OrderSettlementActionResponse,
    response_model_exclude_none=True,
)
def reject_valid_preview(
    order_id: str,
    mode: str = Query(default="server_broadcast"),
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> OrderSettlementActionResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not _can_reject_valid_preview(order, db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order cannot reject valid preview")
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.reject_valid_preview(order)
    if action_mode == "user_sign":
        return _user_sign_order_action_response(order=order, write_result=write_result)

    try:
        broadcast = onchain_lifecycle.send_as_user(user_id=order.user_id, write_result=write_result)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain buyer signer is not configured: {exc}",
        ) from exc

    _project_authoritative_order_status(
        order=order,
        order_status="REJECTED",
        event_id=_authoritative_event_id_from_broadcast(broadcast),
        db=db,
    )
    db.commit()
    db.refresh(order)

    return OrderSettlementActionResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        tx_hash=broadcast.tx_hash,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
    )


@router.post(
    "/{order_id}/refund-failed-or-no-valid-preview",
    response_model=OrderSettlementActionResponse,
    response_model_exclude_none=True,
)
def refund_failed_or_no_valid_preview(
    order_id: str,
    mode: str = Query(default="server_broadcast"),
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> OrderSettlementActionResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not _can_refund_failed_or_no_valid_preview(order, db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order cannot trigger failed-preview refund")
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.refund_failed_or_no_valid_preview(order)
    if action_mode == "user_sign":
        return _user_sign_order_action_response(order=order, write_result=write_result)

    try:
        broadcast = onchain_lifecycle.send_as_user(user_id=order.user_id, write_result=write_result)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain buyer signer is not configured: {exc}",
        ) from exc

    _project_authoritative_order_status(
        order=order,
        order_status="REFUNDED",
        event_id=_authoritative_event_id_from_broadcast(broadcast),
        db=db,
    )
    db.commit()
    db.refresh(order)

    return OrderSettlementActionResponse(
        order_id=order.id,
        state=order.state,
        settlement_state=order.settlement_state,
        tx_hash=broadcast.tx_hash,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
    )


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)) -> OrderResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return _serialize_order(order, db=db)


@router.post("/{order_id}/mock-result-ready", response_model=ResultReadyResponse)
def mock_mark_result_ready(
    order_id: str,
    payload: ResultReadyRequest | None = None,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> ResultReadyResponse:
    _ensure_demo_write_allowed()
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order.state != OrderState.RESULT_CONFIRMED:
        order.state = OrderState.RESULT_PENDING_CONFIRMATION
    order.execution_state = ExecutionState.SUCCEEDED
    order.preview_state = PreviewState.READY
    valid_preview = True if payload is None else payload.valid_preview
    metadata_updates = {"preview_valid": valid_preview}
    merged_metadata = dict(order.execution_metadata or {})
    merged_metadata.update(metadata_updates)
    order.execution_metadata = merged_metadata
    db.add(order)
    db.flush()
    if onchain_lifecycle.enabled() and order.onchain_order_id:
        machine = db.get(Machine, order.machine_id)
        if machine is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
        try:
            broadcast = onchain_lifecycle.send_as_user(
                user_id=machine.owner_user_id,
                write_result=order_writer.mark_preview_ready(order, valid_preview=valid_preview),
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Onchain machine-owner signer is not configured: {exc}",
            ) from exc
        _project_authoritative_order_status(
            order=order,
            order_status="PREVIEW_READY",
            event_id=_authoritative_event_id_from_broadcast(broadcast),
            db=db,
        )
        metadata_updates["onchain_preview_ready_tx_hash"] = broadcast.tx_hash
        merged_metadata = dict(order.execution_metadata or {})
        merged_metadata.update(metadata_updates)
        order.execution_metadata = merged_metadata
        db.add(order)
    else:
        order_writer.mark_preview_ready(order, valid_preview=valid_preview)
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
) -> ExecutionRunResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    metadata = dict(order.execution_metadata or {})
    if order.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order is expired" if metadata.get("cancelled_as_expired") or order.preview_state == PreviewState.EXPIRED else "Order is cancelled",
        )
    if order.onchain_order_id is None or not _has_authoritative_paid_projection(order):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order execution requires authoritative paid projection",
        )
    if not is_order_execution_contract_consistent(order):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order execution contract is inconsistent",
        )

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    if not _machine_is_runtime_available(order, machine.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order machine is unavailable",
        )
    if _has_active_execution_run(order.id, db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Execution already in progress for this order",
        )

    intent_context = {}
    selected_native_plan_index = (order.execution_metadata or {}).get("selected_native_plan_index")
    intent_context["machine_id"] = machine.id
    if selected_native_plan_index is not None:
        intent_context["selected_native_plan_index"] = str(selected_native_plan_index)
    if metadata.get("planning_context_id"):
        intent_context["planning_context_id"] = str(metadata["planning_context_id"])

    try:
        dispatch_input_files = stage_bound_execution_input_files(
            db=db,
            input_files=tuple((order.execution_request or {}).get("files") or ()),
            attachment_session_id=(order.execution_metadata or {}).get("attachment_session_id"),
            attachment_ids=tuple((order.execution_metadata or {}).get("attachment_ids") or ()),
        )
    except AttachmentResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    dispatch = ExecutionEngineService(execution_service=execution_service).dispatch(
        IntentRequest(
            intent_id=order.id,
            prompt=str((order.execution_request or {}).get("intent") or order.user_prompt),
            input_files=dispatch_input_files,
            execution_strategy=ExecutionStrategy((order.execution_request or {}).get("execution_strategy", "quality")),
            context=intent_context,
        )
    )
    if not dispatch.accepted or dispatch.run_id is None or dispatch.run_status is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution dispatch rejected")

    run_status = ExecutionRunStatus(dispatch.run_status.value)
    submission_payload = dict(order.execution_request or {})
    if metadata.get("selected_plan_id") is not None:
        submission_payload["selected_plan_id"] = metadata.get("selected_plan_id")
    if metadata.get("selected_plan_strategy") is not None:
        submission_payload["selected_plan_strategy"] = metadata.get("selected_plan_strategy")
    if selected_native_plan_index is not None:
        submission_payload["selected_plan_index"] = selected_native_plan_index
    run = ExecutionRun(
        id=dispatch.run_id,
        order_id=order.id,
        machine_id=machine.id,
        viewer_user_id=order.user_id,
        run_kind="order",
        external_order_id=order.id,
        status=run_status,
        submission_payload=submission_payload,
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
    response = ExecutionRunResponse.model_validate(run)
    selected_plan = build_selected_plan_payload(
        order=order,
        submission_payload=run.submission_payload,
        snapshot_selected_plan=dispatch.selected_plan,
    )
    return response.model_copy(
        update={
            "selected_plan": selected_plan,
            "selected_plan_binding": build_selected_plan_binding(
                order=order,
                selected_plan=selected_plan,
                submission_payload=run.submission_payload,
            ),
        }
    )
