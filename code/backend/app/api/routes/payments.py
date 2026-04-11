import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import PaymentState
from app.domain.models import Machine, Order, Payment, utc_now
from app.domain.order_truth import set_authoritative_order_truth
from app.domain.rules import has_sufficient_payment, is_dividend_eligible
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt
from app.integrations.onchain_broadcaster import OnchainBroadcaster, get_onchain_broadcaster
from app.integrations.onchain_payment_verifier import OnchainPaymentVerifier, get_onchain_payment_verifier
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriter, get_order_writer
from app.onchain.tx_sender import TransactionSender, get_onchain_transaction_sender
from app.onchain.tx_sender import encode_contract_call
from app.runtime.cost_service import RuntimeCostService, get_runtime_cost_service
from app.schemas.payment import (
    DirectPaymentFinalizeRequest,
    DirectPaymentFinalizeResponse,
    DirectPaymentIntentRequest,
    DirectPaymentIntentResponse,
    DirectPaymentSyncRequest,
    DirectPaymentSyncResponse,
    MockPaymentConfirmRequest,
    MockPaymentConfirmResponse,
    PaymentIntentRequest,
    PaymentIntentResponse,
)

router = APIRouter()
PWR_WEI_MULTIPLIER = Decimal("1000000000000000000")


TERMINAL_PAYMENT_STATES = {PaymentState.SUCCEEDED, PaymentState.FAILED, PaymentState.REFUNDED}
HSP_STABLECOIN_CURRENCIES = {"USDC", "USDT"}


def _ensure_demo_write_allowed(*, detail: str) -> None:
    if get_settings().env not in {"dev", "test"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def _existing_active_hsp_payment(order_id: str, db: Session) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.order_id == order_id,
            Payment.provider == "hsp",
            Payment.state.in_((PaymentState.PENDING, PaymentState.SUCCEEDED)),
        )
    )


def _succeeded_payment_total_cents(order_id: str, db: Session) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id,
            Payment.state == PaymentState.SUCCEEDED,
        )
    )


def _freeze_settlement_policy_if_fully_paid(order: Order, db: Session) -> bool:
    paid_cents = _succeeded_payment_total_cents(order.id, db)
    effective_paid_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
    if not has_sufficient_payment(order.quoted_amount_cents, effective_paid_cents):
        return False

    machine = db.get(Machine, order.machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if order.settlement_beneficiary_user_id is None:
        dividend_eligible = is_dividend_eligible(order.user_id, machine.owner_user_id)
        order.settlement_beneficiary_user_id = machine.owner_user_id
        order.settlement_is_self_use = not dividend_eligible
        order.settlement_is_dividend_eligible = dividend_eligible
    machine.has_active_tasks = True
    db.add(order)
    db.add(machine)
    return True


def _pwr_quote_to_wei_string(pwr_quote: str) -> str:
    return str(int((Decimal(pwr_quote) * PWR_WEI_MULTIPLIER).to_integral_value()))


def _normalize_signature(signature: str) -> str:
    normalized = str(signature).strip().lower()
    if not normalized.startswith("0x"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature must be 0x-prefixed")
    if len(normalized) != 132:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature must be 65 bytes")
    return normalized


def _split_signature(signature: str) -> tuple[int, str, str]:
    normalized = _normalize_signature(signature)[2:]
    r = "0x" + normalized[:64]
    s = "0x" + normalized[64:128]
    v = int(normalized[128:130], 16)
    if v in {0, 1}:
        v += 27
    return v, r, s


def _build_direct_signing_request(
    *,
    currency: str,
    buyer_wallet_address: str | None,
    amount_cents: int,
    token_address: str,
    router_address: str,
    settlement_escrow: str,
    chain_id: int,
    permit2_address: str,
) -> dict[str, Any] | None:
    settings = get_settings()
    if currency == "PWR":
        return None
    if currency == "USDC":
        if not buyer_wallet_address:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="wallet_address is required for USDC direct payment intent",
            )
        valid_after = int((utc_now() - timedelta(minutes=1)).timestamp())
        valid_before = int((utc_now() + timedelta(minutes=30)).timestamp())
        nonce = "0x" + secrets.token_hex(32)
        return {
            "kind": "eip712",
            "primaryType": "ReceiveWithAuthorization",
            "domain": {
                "name": settings.onchain_usdc_eip3009_name,
                "version": settings.onchain_usdc_eip3009_version,
                "chainId": chain_id,
                "verifyingContract": token_address,
            },
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "ReceiveWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "message": {
                "from": buyer_wallet_address.lower(),
                "to": settlement_escrow,
                "value": str(amount_cents),
                "validAfter": str(valid_after),
                "validBefore": str(valid_before),
                "nonce": nonce,
            },
        }
    return {
        "kind": "eip712",
        "primaryType": "PermitTransferFrom",
        "domain": {
            "name": settings.onchain_permit2_name,
            "chainId": chain_id,
            "verifyingContract": permit2_address,
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "PermitTransferFrom": [
                {"name": "permitted", "type": "TokenPermissions"},
                {"name": "spender", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
            "TokenPermissions": [
                {"name": "token", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
        },
        "message": {
            "permitted": {
                "token": token_address,
                "amount": str(amount_cents),
            },
            "spender": router_address,
            "nonce": str(secrets.randbits(128)),
            "deadline": str(int((utc_now() + timedelta(minutes=30)).timestamp())),
        },
    }


def _direct_wallet_submit_payload(direct_intent) -> tuple[str, dict[str, Any]]:
    calldata = encode_contract_call(direct_intent)
    if calldata is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unsupported direct intent method: {direct_intent.method_name}",
        )
    wallet_submit_payload = {
        "to": direct_intent.contract_address,
        "data": calldata,
        "value": "0x0",
        **direct_intent.payload,
    }
    return calldata, wallet_submit_payload


def _apply_payment_state(
    payment: Payment,
    *,
    state: PaymentState,
    db: Session,
    order_writer: OrderWriter,
    write_chain: bool = True,
) -> Order:
    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if payment.state in TERMINAL_PAYMENT_STATES and payment.state != state:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is already in terminal state")
    if payment.state == state:
        return order

    payment.state = state
    db.add(payment)
    db.flush()
    if state == PaymentState.SUCCEEDED and _freeze_settlement_policy_if_fully_paid(order, db) and write_chain:
        order_writer.mark_order_paid(order, payment)
    return order


def _persist_order_chain_anchor(
    order: Order,
    *,
    onchain_order_id: str,
    create_order_tx_hash: str,
    create_order_event_id: str,
    create_order_block_number: int,
) -> None:
    conflict_fields = (
        ("onchain_order_id", order.onchain_order_id, onchain_order_id),
        ("create_order_tx_hash", order.create_order_tx_hash, create_order_tx_hash),
        ("create_order_event_id", order.create_order_event_id, create_order_event_id),
        ("create_order_block_number", order.create_order_block_number, create_order_block_number),
    )
    for field_name, existing_value, new_value in conflict_fields:
        if existing_value is not None and existing_value != new_value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Order already anchored with conflicting {field_name}",
            )

    order.onchain_order_id = onchain_order_id
    order.create_order_tx_hash = create_order_tx_hash
    order.create_order_event_id = create_order_event_id
    order.create_order_block_number = create_order_block_number


def _persist_order_tx_correlation(order: Order, *, payment_tx_hash: str) -> None:
    metadata = dict(order.execution_metadata or {})
    existing = metadata.get("last_payment_tx_hash")
    if existing is not None and existing != payment_tx_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order already correlated with a different payment tx hash",
        )
    metadata["last_payment_tx_hash"] = payment_tx_hash
    order.execution_metadata = metadata


def _backfill_order_chain_anchor_from_verification(order: Order, verification) -> None:
    if verification.state != PaymentState.SUCCEEDED:
        return
    if (
        verification.evidence_order_id is None
        or verification.evidence_create_order_tx_hash is None
        or verification.evidence_create_order_event_id is None
        or verification.evidence_create_order_block_number is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onchain evidence missing create+paid anchor fields",
        )
    _persist_order_chain_anchor(
        order,
        onchain_order_id=verification.evidence_order_id,
        create_order_tx_hash=verification.evidence_create_order_tx_hash,
        create_order_event_id=verification.evidence_create_order_event_id,
        create_order_block_number=verification.evidence_create_order_block_number,
    )


def _backfill_order_chain_anchor_from_receipt(order: Order, receipt: OnchainCreateOrderReceipt) -> None:
    _persist_order_chain_anchor(
        order,
        onchain_order_id=receipt.onchain_order_id,
        create_order_tx_hash=receipt.tx_hash,
        create_order_event_id=receipt.event_id,
        create_order_block_number=receipt.block_number,
    )


def _mark_authoritative_paid_projection(
    order: Order,
    *,
    order_status: str,
    event_id: str,
) -> None:
    set_authoritative_order_truth(order, order_status=order_status, event_id=event_id)


def _mark_authoritative_order_created_projection(order: Order, *, event_id: str) -> None:
    set_authoritative_order_truth(order, order_status="CREATED", event_id=event_id)


def _resolve_buyer_wallet_or_409(*, container: Container, order: Order, detail: str) -> str:
    buyer_wallet_address = container.buyer_address_resolver.resolve_wallet(order.user_id)
    if buyer_wallet_address is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return buyer_wallet_address.lower()


def _ensure_onchain_order_anchor(
    *,
    order: Order,
    container: Container,
    order_writer: OrderWriter,
    onchain_broadcaster: OnchainBroadcaster,
    tx_sender: TransactionSender,
    db: Session,
    unresolved_wallet_detail: str,
) -> None:
    if order.onchain_order_id is not None:
        return
    buyer_wallet_address = _resolve_buyer_wallet_or_409(
        container=container,
        order=order,
        detail=unresolved_wallet_detail,
    )
    write_result = order_writer.create_order(order, buyer_wallet_address=buyer_wallet_address)
    broadcasted_write = tx_sender.send(write_result)
    create_order_receipt = onchain_broadcaster.broadcast_create_order(write_result=broadcasted_write)
    db.refresh(order)
    _backfill_order_chain_anchor_from_receipt(order, create_order_receipt)
    _mark_authoritative_order_created_projection(order, event_id=create_order_receipt.event_id)
    db.add(order)


@router.post(
    "/orders/{order_id}/intent",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create HSP stablecoin checkout",
    description="Formal stablecoin checkout path for OutcomeX. Supported stablecoins are USDC and USDT via HSP.",
)
def create_payment_intent(
    order_id: str,
    payload: PaymentIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
    order_writer: OrderWriter = Depends(get_order_writer),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
) -> PaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payload.currency.upper()
    if currency not in HSP_STABLECOIN_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HSP checkout only supports USDC or USDT stablecoins",
        )

    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="HSP payment amount must match quoted order amount",
        )
    if order.is_cancelled or order.is_expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not payable")

    _ensure_onchain_order_anchor(
        order=order,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
        db=db,
        unresolved_wallet_detail="Buyer wallet address unresolved for HSP settlement",
    )

    existing_payment = _existing_active_hsp_payment(order.id, db)
    if existing_payment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active HSP payment already exists for this order",
        )

    try:
        merchant_order = container.hsp_adapter.create_payment_intent(
            order_id=order.id,
            amount_cents=payload.amount_cents,
            currency=currency,
            expires_at=order.unpaid_expiry_at,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    payment = Payment(
        order_id=order.id,
        provider=merchant_order.provider,
        provider_reference=merchant_order.provider_reference,
        merchant_order_id=merchant_order.merchant_order_id,
        flow_id=merchant_order.flow_id,
        checkout_url=merchant_order.payment_url,
        provider_payload=merchant_order.provider_payload,
        amount_cents=payload.amount_cents,
        currency=currency,
        state=PaymentState.PENDING,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return PaymentIntentResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider=payment.provider,
        provider_reference=merchant_order.provider_reference,
        checkout_url=merchant_order.payment_url,
        flow_id=merchant_order.flow_id,
        merchant_order_id=merchant_order.merchant_order_id,
        state=payment.state,
        quote=cost_service.quote_for_order_amount(order.quoted_amount_cents),
        created_at=payment.created_at,
    )


@router.post(
    "/orders/{order_id}/direct-intent",
    response_model=DirectPaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create legacy direct payment intent",
    description="Legacy compatibility route for direct onchain payment intents. Formal stablecoin checkout should use the HSP route instead.",
    deprecated=True,
)
def create_direct_payment_intent(
    order_id: str,
    payload: DirectPaymentIntentRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
    container: Container = Depends(get_dependency_container),
    onchain_broadcaster: OnchainBroadcaster = Depends(get_onchain_broadcaster),
    tx_sender: TransactionSender = Depends(get_onchain_transaction_sender),
) -> DirectPaymentIntentResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payload.currency.upper()
    if currency not in {"USDC", "USDT", "PWR"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")
    if currency in HSP_STABLECOIN_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Direct stablecoin checkout is legacy-only; use the HSP payment intent route",
        )
    if payload.amount_cents != order.quoted_amount_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Direct payment amount must match quoted order amount",
        )
    if order.is_cancelled or order.is_expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not payable")

    _ensure_onchain_order_anchor(
        order=order,
        container=container,
        order_writer=order_writer,
        onchain_broadcaster=onchain_broadcaster,
        tx_sender=tx_sender,
        db=db,
        unresolved_wallet_detail="Buyer wallet address unresolved for direct payment",
    )

    payment = Payment(
        order_id=order.id,
        provider="onchain_router",
        amount_cents=payload.amount_cents,
        currency=currency,
        state=PaymentState.PENDING,
    )
    db.add(payment)
    db.flush()

    quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    if currency == "PWR":
        direct_intent = order_writer.build_direct_payment_intent(
            order,
            payment,
            pwr_amount=_pwr_quote_to_wei_string(quote.pwr_quote),
            pricing_version=quote.pricing_version,
            pwr_anchor_price_cents=quote.pwr_anchor_price_cents,
        )
    else:
        direct_intent = order_writer.build_direct_payment_intent(order, payment)
    payment.provider_reference = direct_intent.method_name
    payment.merchant_order_id = order.id
    payment.flow_id = payment.id
    registry = ContractsRegistry()
    signing_request = _build_direct_signing_request(
        currency=currency,
        buyer_wallet_address=payload.wallet_address,
        amount_cents=order.quoted_amount_cents,
        token_address=str(direct_intent.payload.get("token_address") or registry.payment_token(currency)),
        router_address=direct_intent.contract_address,
        settlement_escrow=registry.settlement_controller().contract_address,
        chain_id=direct_intent.chain_id,
        permit2_address=registry.permit2().contract_address,
    )
    finalize_required = signing_request is not None
    calldata = None
    wallet_submit_payload = None
    if not finalize_required:
        calldata, wallet_submit_payload = _direct_wallet_submit_payload(direct_intent)
    payment.provider_payload = {
        "direct_intent_payload": direct_intent.payload,
        "signing_request": signing_request,
    }
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return DirectPaymentIntentResponse(
        payment_id=payment.id,
        order_id=order.id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
        finalize_required=finalize_required,
        signing_request=signing_request,
        submit_payload=wallet_submit_payload,
        calldata=calldata,
        state=payment.state,
        quote=quote,
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/finalize-intent", response_model=DirectPaymentFinalizeResponse)
def finalize_direct_payment_intent(
    payment_id: str,
    payload: DirectPaymentFinalizeRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
    cost_service: RuntimeCostService = Depends(get_runtime_cost_service),
) -> DirectPaymentFinalizeResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "onchain_router":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an onchain router payment")

    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    currency = payment.currency.upper()
    quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    if currency == "PWR":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PWR direct payments do not require finalize")
    direct_intent = order_writer.build_direct_payment_intent(order, payment)
    if payment.provider_payload and payment.provider_payload.get("direct_intent_payload"):
        direct_intent.payload.update(dict(payment.provider_payload["direct_intent_payload"]))

    signing_request = payment.provider_payload.get("signing_request") if payment.provider_payload else None
    if signing_request is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Direct payment signing request not found")

    if currency == "USDC":
        message = signing_request["message"]
        v, r, s = _split_signature(payload.signature)
        direct_intent.payload.update(
            {
                "valid_after": message["validAfter"],
                "valid_before": message["validBefore"],
                "nonce": message["nonce"],
                "v": v,
                "r": r,
                "s": s,
            }
        )
    elif currency == "USDT":
        message = signing_request["message"]
        direct_intent.payload.update(
            {
                "permit_nonce": message["nonce"],
                "deadline": message["deadline"],
                "signature": _normalize_signature(payload.signature),
            }
        )
    else:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported direct payment currency")

    calldata, wallet_submit_payload = _direct_wallet_submit_payload(direct_intent)
    return DirectPaymentFinalizeResponse(
        payment_id=payment.id,
        order_id=order.id,
        provider=payment.provider,
        contract_name=direct_intent.contract_name,
        contract_address=direct_intent.contract_address,
        chain_id=direct_intent.chain_id,
        method_name=direct_intent.method_name,
        signing_standard=str(direct_intent.payload["signing_standard"]),
        finalize_required=False,
        signing_request=None,
        submit_payload=wallet_submit_payload,
        calldata=calldata,
        state=payment.state,
        quote=quote,
        created_at=payment.created_at,
    )


@router.post("/{payment_id}/sync-onchain", response_model=DirectPaymentSyncResponse)
def sync_onchain_payment(
    payment_id: str,
    payload: DirectPaymentSyncRequest,
    db: Session = Depends(get_db),
    verifier: OnchainPaymentVerifier = Depends(get_onchain_payment_verifier),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> DirectPaymentSyncResponse:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.provider != "onchain_router":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not an onchain router payment")
    if payload.state == PaymentState.CREATED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sync state must be pending or terminal")

    order = db.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    verification = verifier.verify_payment(
        tx_hash=payload.tx_hash,
        wallet_address=payload.wallet_address,
        order=order,
        payment=payment,
    )
    if not verification.matched:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Onchain evidence verification failed: {verification.reason or 'mismatch'}",
        )
    if verification.state != PaymentState.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onchain receipt did not confirm a successful payment",
        )

    db.refresh(order)
    db.refresh(payment)

    if (
        order.onchain_order_id is None
        or order.create_order_tx_hash is None
        or order.create_order_event_id is None
        or order.create_order_block_number is None
    ):
        _backfill_order_chain_anchor_from_verification(order, verification)
    _mark_authoritative_paid_projection(order, order_status="PAID", event_id=verification.event_id)
    _persist_order_tx_correlation(order, payment_tx_hash=verification.tx_hash)
    db.add(order)

    payment.callback_event_id = verification.event_id
    payment.callback_state = verification.state.value
    payment.callback_received_at = utc_now()
    payment.callback_tx_hash = verification.tx_hash
    _apply_payment_state(payment, state=PaymentState.SUCCEEDED, db=db, order_writer=order_writer, write_chain=False)
    db.add(payment)
    db.commit()
    return DirectPaymentSyncResponse(
        payment_id=payment.id,
        state=payment.state,
        tx_hash=verification.tx_hash,
        synced_onchain=True,
    )


@router.post("/{payment_id}/mock-confirm", response_model=MockPaymentConfirmResponse)
def mock_confirm_payment(
    payment_id: str,
    payload: MockPaymentConfirmRequest,
    db: Session = Depends(get_db),
    order_writer: OrderWriter = Depends(get_order_writer),
) -> MockPaymentConfirmResponse:
    _ensure_demo_write_allowed(detail="Mock payment confirmation is only available in dev/test")
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payload.state not in {PaymentState.SUCCEEDED, PaymentState.FAILED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mock confirmation only accepts succeeded or failed",
        )

    _apply_payment_state(payment, state=payload.state, db=db, order_writer=order_writer)
    db.commit()
    return MockPaymentConfirmResponse(payment_id=payment.id, state=payment.state)
