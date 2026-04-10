from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Machine, PrimaryIssuancePurchase, PrimaryIssuanceSku, utc_now
from app.integrations.hsp_adapter import HSPWebhookEvent
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.schemas.primary_issuance import (
    PrimaryIssuancePurchaseIntentRequest,
    PrimaryIssuancePurchaseIntentResponse,
    PrimaryIssuanceSkuResponse,
)

router = APIRouter()

PRIMARY_ISSUANCE_SKU_ID = "apple-silicon-96gb-qwen-family"
PRIMARY_ISSUANCE_DISPLAY_NAME = "Apple Silicon 96GB Unified Memory + Qwen Family"
PRIMARY_ISSUANCE_PROFILE_LABEL = "Qwen Family"
PRIMARY_ISSUANCE_GPU_SPEC = "Apple Silicon 96GB Unified Memory"
PRIMARY_ISSUANCE_MODEL_FAMILY = "Qwen Family"
PRIMARY_ISSUANCE_PRICE_CENTS = 390
PRIMARY_ISSUANCE_CURRENCY = "USDC"
PRIMARY_ISSUANCE_DEFAULT_STOCK = 10


def _ensure_fixed_primary_sku(db: Session) -> PrimaryIssuanceSku:
    sku = db.get(PrimaryIssuanceSku, PRIMARY_ISSUANCE_SKU_ID)
    if sku is not None:
        return sku

    sku = PrimaryIssuanceSku(
        sku_id=PRIMARY_ISSUANCE_SKU_ID,
        display_name=PRIMARY_ISSUANCE_DISPLAY_NAME,
        profile_label=PRIMARY_ISSUANCE_PROFILE_LABEL,
        gpu_spec=PRIMARY_ISSUANCE_GPU_SPEC,
        model_family=PRIMARY_ISSUANCE_MODEL_FAMILY,
        price_cents=PRIMARY_ISSUANCE_PRICE_CENTS,
        currency=PRIMARY_ISSUANCE_CURRENCY,
        stock_available=PRIMARY_ISSUANCE_DEFAULT_STOCK,
    )
    db.add(sku)
    db.flush()
    return sku


def _sku_to_response(sku: PrimaryIssuanceSku) -> PrimaryIssuanceSkuResponse:
    return PrimaryIssuanceSkuResponse(
        sku_id=sku.sku_id,
        display_name=sku.display_name,
        profile_label=sku.profile_label,
        gpu_spec=sku.gpu_spec,
        model_family=sku.model_family,
        price_cents=sku.price_cents,
        currency=sku.currency,
        stock_available=sku.stock_available,
    )


def _normalize_hsp_tx_hash(raw_tx_hash: str | None) -> str | None:
    if raw_tx_hash is None:
        return None
    normalized = raw_tx_hash.strip().lower()
    return normalized or None


def _mark_primary_purchase_callback(*, purchase: PrimaryIssuancePurchase, event: HSPWebhookEvent) -> None:
    purchase.callback_event_id = event.event_id
    purchase.callback_state = event.status
    purchase.callback_received_at = utc_now()
    purchase.callback_tx_hash = _normalize_hsp_tx_hash(event.tx_hash)


def _require_primary_success_tx_hash(event: HSPWebhookEvent) -> str:
    normalized_tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
    if normalized_tx_hash is None or not normalized_tx_hash.startswith("0x"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Successful primary webhook must include tx signature",
        )
    return normalized_tx_hash


def _assert_primary_tx_hash_not_reused(
    *,
    purchase: PrimaryIssuancePurchase,
    normalized_tx_hash: str,
    db: Session,
) -> None:
    reused_tx = db.scalar(
        select(PrimaryIssuancePurchase.id).where(
            PrimaryIssuancePurchase.id != purchase.id,
            PrimaryIssuancePurchase.state == PaymentState.SUCCEEDED,
            func.lower(PrimaryIssuancePurchase.callback_tx_hash) == normalized_tx_hash,
        )
    )
    if reused_tx is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Primary tx signature already used by another purchase",
        )


def _release_primary_stock_reservation_if_needed(*, purchase: PrimaryIssuancePurchase, db: Session) -> None:
    if not purchase.stock_reserved:
        return
    sku = db.get(PrimaryIssuanceSku, purchase.sku_id)
    if sku is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance SKU not found")
    sku.stock_available += 1
    purchase.stock_reserved = False
    purchase.stock_released_at = utc_now()
    db.add(sku)
    db.add(purchase)


def _resolve_primary_purchase_for_hsp_event(*, event: HSPWebhookEvent, db: Session) -> PrimaryIssuancePurchase | None:
    purchase = db.scalar(
        select(PrimaryIssuancePurchase).where(PrimaryIssuancePurchase.provider_reference == event.payment_request_id)
    )
    if purchase is not None:
        return purchase
    purchase = db.scalar(select(PrimaryIssuancePurchase).where(PrimaryIssuancePurchase.merchant_order_id == event.cart_mandate_id))
    if purchase is not None:
        return purchase
    if event.flow_id:
        purchase = db.scalar(select(PrimaryIssuancePurchase).where(PrimaryIssuancePurchase.flow_id == event.flow_id))
    return purchase


def _finalize_primary_purchase_success(
    *,
    purchase: PrimaryIssuancePurchase,
    sku: PrimaryIssuanceSku,
    container: Container,
    onchain_lifecycle: OnchainLifecycleService,
    db: Session,
) -> None:
    if purchase.minted_machine_id is not None:
        return

    if not purchase.stock_reserved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance stock not reserved")

    if not onchain_lifecycle.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance mint unavailable")

    owner_wallet = container.buyer_address_resolver.resolve_wallet(purchase.buyer_user_id)
    if owner_wallet is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Buyer wallet address unresolved")

    minted = onchain_lifecycle.mint_machine_for_owner(
        owner_user_id=purchase.buyer_user_id,
        token_uri=f"ipfs://outcomex-machine/primary-issuance/{purchase.id}",
    )
    if minted.onchain_machine_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mint receipt missing machine id")

    machine = db.scalar(select(Machine).where(Machine.onchain_machine_id == minted.onchain_machine_id))
    if machine is None:
        machine = Machine(
            display_name=sku.display_name,
            owner_user_id=purchase.buyer_user_id,
            owner_chain_address=owner_wallet.lower(),
            ownership_source="chain",
            onchain_machine_id=minted.onchain_machine_id,
        )
        db.add(machine)
        db.flush()

    purchase.minted_machine_id = machine.id
    purchase.minted_onchain_machine_id = machine.onchain_machine_id
    purchase.stock_reserved = False
    purchase.finalized_at = utc_now()
    db.add(purchase)


def apply_primary_purchase_hsp_webhook(
    *,
    purchase: PrimaryIssuancePurchase,
    mapped_state: PaymentState,
    event: HSPWebhookEvent,
    container: Container,
    onchain_lifecycle: OnchainLifecycleService,
    db: Session,
) -> dict[str, object]:
    if purchase.callback_event_id == event.event_id:
        return {
            "purchase_id": purchase.id,
            "state": purchase.state.value,
            "duplicate": True,
            "minted_machine_id": purchase.minted_machine_id,
            "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
        }

    if event.amount_cents != purchase.amount_cents:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP webhook amount mismatch")
    if event.currency != purchase.currency.upper():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSP webhook currency mismatch")

    if mapped_state == PaymentState.SUCCEEDED:
        normalized_tx_hash = _require_primary_success_tx_hash(event)
        _assert_primary_tx_hash_not_reused(
            purchase=purchase,
            normalized_tx_hash=normalized_tx_hash,
            db=db,
        )
        if purchase.state == PaymentState.SUCCEEDED:
            _mark_primary_purchase_callback(purchase=purchase, event=event)
            db.add(purchase)
            return {
                "purchase_id": purchase.id,
                "state": purchase.state.value,
                "duplicate": True,
                "minted_machine_id": purchase.minted_machine_id,
                "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
            }
        if purchase.state in {PaymentState.FAILED, PaymentState.REFUNDED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Primary issuance purchase is already in terminal state",
            )

        purchase.state = PaymentState.SUCCEEDED
        _mark_primary_purchase_callback(purchase=purchase, event=event)
        db.add(purchase)
        # Persist a success marker before minting so retries fail closed.
        db.commit()
        db.refresh(purchase)

        sku = db.get(PrimaryIssuanceSku, purchase.sku_id)
        if sku is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance SKU not found")
        _finalize_primary_purchase_success(
            purchase=purchase,
            sku=sku,
            container=container,
            onchain_lifecycle=onchain_lifecycle,
            db=db,
        )
        return {
            "purchase_id": purchase.id,
            "state": purchase.state.value,
            "duplicate": False,
            "minted_machine_id": purchase.minted_machine_id,
            "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
        }

    if purchase.state == PaymentState.SUCCEEDED:
        _mark_primary_purchase_callback(purchase=purchase, event=event)
        db.add(purchase)
        return {
            "purchase_id": purchase.id,
            "state": purchase.state.value,
            "duplicate": True,
            "minted_machine_id": purchase.minted_machine_id,
            "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
        }

    if purchase.state in {PaymentState.FAILED, PaymentState.REFUNDED} and purchase.state != mapped_state:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance purchase is already in terminal state")

    if mapped_state in {PaymentState.FAILED, PaymentState.REFUNDED}:
        _release_primary_stock_reservation_if_needed(purchase=purchase, db=db)

    purchase.state = mapped_state
    _mark_primary_purchase_callback(purchase=purchase, event=event)
    db.add(purchase)

    return {
        "purchase_id": purchase.id,
        "state": purchase.state.value,
        "duplicate": False,
        "minted_machine_id": purchase.minted_machine_id,
        "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
    }


@router.get("/skus", response_model=list[PrimaryIssuanceSkuResponse])
def list_primary_issuance_skus(db: Session = Depends(get_db)) -> list[PrimaryIssuanceSkuResponse]:
    sku = _ensure_fixed_primary_sku(db)
    db.commit()
    db.refresh(sku)
    return [_sku_to_response(sku)]


@router.post(
    "/skus/{sku_id}/purchase-intent",
    response_model=PrimaryIssuancePurchaseIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_primary_issuance_purchase_intent(
    sku_id: str,
    payload: PrimaryIssuancePurchaseIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
) -> PrimaryIssuancePurchaseIntentResponse:
    if sku_id != PRIMARY_ISSUANCE_SKU_ID:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Primary issuance SKU not found")

    sku = _ensure_fixed_primary_sku(db)
    if sku.stock_available <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance stock exhausted")

    buyer_wallet = container.buyer_address_resolver.resolve_wallet(payload.buyer_user_id)
    if buyer_wallet is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Buyer wallet address unresolved")

    purchase = PrimaryIssuancePurchase(
        sku_id=sku.sku_id,
        buyer_user_id=payload.buyer_user_id,
        amount_cents=sku.price_cents,
        currency=sku.currency,
        state=PaymentState.PENDING,
        stock_reserved=True,
    )
    sku.stock_available -= 1
    db.add(sku)
    db.add(purchase)
    db.flush()

    try:
        merchant_order = container.hsp_adapter.create_payment_intent(
            order_id=purchase.id,
            amount_cents=purchase.amount_cents,
            currency=purchase.currency,
            expires_at=None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    purchase.provider = merchant_order.provider
    purchase.provider_reference = merchant_order.provider_reference
    purchase.merchant_order_id = merchant_order.merchant_order_id
    purchase.flow_id = merchant_order.flow_id
    purchase.checkout_url = merchant_order.payment_url
    purchase.provider_payload = merchant_order.provider_payload
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    return PrimaryIssuancePurchaseIntentResponse(
        purchase_id=purchase.id,
        sku_id=purchase.sku_id,
        buyer_user_id=purchase.buyer_user_id,
        provider=purchase.provider,
        provider_reference=purchase.provider_reference or "",
        merchant_order_id=purchase.merchant_order_id,
        flow_id=purchase.flow_id,
        checkout_url=purchase.checkout_url or "",
        amount_cents=purchase.amount_cents,
        currency=purchase.currency,
        state=purchase.state,
        created_at=purchase.created_at,
    )
