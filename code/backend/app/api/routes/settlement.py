from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState, SettlementState
from app.domain.models import Machine, Order, Payment, SettlementRecord
from app.domain.settlement_projection import ensure_confirmed_settlement_projection
from app.domain.rules import calculate_revenue_split, can_start_settlement, has_sufficient_payment
from app.onchain.claim_state_reader import SettlementClaimStateReader, get_settlement_claim_state_reader
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import encode_contract_call
from app.schemas.settlement import (
    PlatformRevenueClaimRequest,
    PlatformRevenueClaimResponse,
    RefundClaimResponse,
    SettlementPreviewResponse,
    SettlementStartResponse,
)

router = APIRouter()


def _normalize_action_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in {"server_broadcast", "user_sign"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action mode")
    return normalized


DEFAULT_USER_ACTION_MODE = "user_sign"


def _user_sign_refund_claim_response(*, order: Order, currency: str, claimant_user_id: str, write_result) -> RefundClaimResponse:
    return RefundClaimResponse(
        order_id=order.id,
        claimant_user_id=claimant_user_id,
        currency=currency.upper(),
        mode="user_sign",
        chain_id=write_result.chain_id,
        contract_address=write_result.contract_address,
        contract_name=write_result.contract_name,
        method_name=write_result.method_name,
        submit_payload=write_result.payload,
        calldata=encode_contract_call(write_result),
    )


def _user_sign_platform_claim_response(*, currency: str, write_result) -> PlatformRevenueClaimResponse:
    return PlatformRevenueClaimResponse(
        currency=currency.upper(),
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
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    if not has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents):
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
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    platform_fee_cents, machine_share_cents = calculate_revenue_split(gross_amount_cents)
    return SettlementPreviewResponse(
        order_id=order.id,
        gross_amount_cents=gross_amount_cents,
        platform_fee_cents=platform_fee_cents,
        machine_share_cents=machine_share_cents,
        state=SettlementState.READY,
    )


@router.post("/orders/{order_id}/start", response_model=SettlementStartResponse)
def start_settlement(
    order_id: str,
    db: Session = Depends(get_db),
) -> SettlementStartResponse:
    order = _validated_order_for_settlement(order_id, db)
    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    existing = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()
    if existing is not None:
        return SettlementStartResponse(
            settlement_id=existing.id,
            order_id=existing.order_id,
            state=existing.state,
            created_at=existing.created_at,
        )

    paid_cents = _succeeded_payment_total_cents(order.id, db)
    gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    platform_fee_cents, machine_share_cents = calculate_revenue_split(gross_amount_cents)

    if get_settings().onchain_rpc_url.strip():
        order.settlement_state = SettlementState.DISTRIBUTED
        settlement, _ = ensure_confirmed_settlement_projection(
            db=db,
            order=order,
            machine=machine,
            gross_amount_cents=gross_amount_cents,
        )
    else:
        order.settlement_state = SettlementState.LOCKED
        settlement = SettlementRecord(
            order_id=order.id,
            gross_amount_cents=gross_amount_cents,
            platform_fee_cents=platform_fee_cents,
            machine_share_cents=machine_share_cents,
            state=SettlementState.LOCKED,
            distributed_at=None,
        )
        machine.has_active_tasks = False
        machine.has_unsettled_revenue = bool(order.settlement_is_dividend_eligible and machine_share_cents > 0)
        db.add(machine)
        db.add(settlement)
        db.flush()

    db.add(order)
    db.commit()
    db.refresh(settlement)
    return SettlementStartResponse(
        settlement_id=settlement.id,
        order_id=settlement.order_id,
        state=settlement.state,
        created_at=settlement.created_at,
    )


def _latest_successful_payment(order_id: str, db: Session) -> Payment | None:
    return db.scalar(
        select(Payment)
        .where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )


@router.post("/orders/{order_id}/claim-refund", response_model=RefundClaimResponse, response_model_exclude_none=True)
def claim_order_refund(
    order_id: str,
    mode: str = Query(default=DEFAULT_USER_ACTION_MODE),
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
    if order.state.value not in {"cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not in a refundable terminal state")

    payment = _latest_successful_payment(order.id, db)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order has no successful payment to refund")
    try:
        refundable_amount = claim_state_reader.refundable_amount(user_id=order.user_id, currency=payment.currency)
    except RuntimeError as exc:
        if str(exc) == "buyer_wallet_unresolved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Buyer wallet is not configured for onchain refund claim",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unable to read onchain refund balance: {exc}",
        ) from exc
    if refundable_amount <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Refund has no claimable onchain balance")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.claim_refund(
        currency=payment.currency,
        user_id=order.user_id,
        order_id=order.id,
    )
    if action_mode == "user_sign":
        return _user_sign_refund_claim_response(
            order=order,
            currency=payment.currency,
            claimant_user_id=order.user_id,
            write_result=write_result,
        )

    try:
        broadcast = onchain_lifecycle.send_as_user(
            user_id=order.user_id,
            write_result=write_result,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain buyer signer is not configured: {exc}",
        ) from exc

    return RefundClaimResponse(
        order_id=order.id,
        claimant_user_id=order.user_id,
        currency=payment.currency.upper(),
        tx_hash=broadcast.tx_hash,
        contract_name="SettlementController",
        method_name="claimRefund",
    )


@router.post("/platform/claim", response_model=PlatformRevenueClaimResponse, response_model_exclude_none=True)
def claim_platform_revenue(
    payload: PlatformRevenueClaimRequest,
    mode: str = Query(default="server_broadcast"),
    order_writer: OrderWriter = Depends(get_order_writer),
    claim_state_reader: SettlementClaimStateReader = Depends(get_settlement_claim_state_reader),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> PlatformRevenueClaimResponse:
    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Onchain runtime is not enabled")
    try:
        platform_amount = claim_state_reader.platform_accrued_amount(currency=payload.currency)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unable to read onchain platform revenue: {exc}",
        ) from exc
    if platform_amount <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Platform has no claimable onchain revenue")

    action_mode = _normalize_action_mode(mode)
    write_result = order_writer.claim_platform_revenue(currency=payload.currency)
    if action_mode == "user_sign":
        return _user_sign_platform_claim_response(currency=payload.currency, write_result=write_result)

    try:
        broadcast = onchain_lifecycle.send_as_treasury(
            write_result=write_result,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain treasury signer is not configured: {exc}",
        ) from exc

    return PlatformRevenueClaimResponse(
        currency=payload.currency.upper(),
        tx_hash=broadcast.tx_hash,
        contract_name="SettlementController",
        method_name="claimPlatformRevenue",
    )
