from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.domain.enums import PaymentState
from app.domain.models import Machine, PrimaryIssuancePurchase, PrimaryIssuanceSku, utc_now
from app.integrations.hsp_adapter import HSPWebhookEvent
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.onchain.receipts import get_receipt_reader
from app.schemas.primary_issuance import (
    PrimaryIssuancePurchaseIntentRequest,
    PrimaryIssuancePurchaseIntentResponse,
    PrimaryIssuancePurchaseSyncResponse,
    PrimaryIssuanceSkuResponse,
)

router = APIRouter()

PRIMARY_ISSUANCE_SKU_ID = "apple-silicon-96gb-qwen-family"
PRIMARY_ISSUANCE_DISPLAY_NAME = "Apple Silicon 96GB Unified Memory + Qwen Family"
PRIMARY_ISSUANCE_PROFILE_LABEL = "Qwen Family"
PRIMARY_ISSUANCE_GPU_SPEC = "Apple Silicon 96GB Unified Memory"
PRIMARY_ISSUANCE_MODEL_FAMILY = "Qwen Family"
PRIMARY_ISSUANCE_PRICE_CENTS = 390
PRIMARY_ISSUANCE_CURRENCY = "USDT"
PRIMARY_ISSUANCE_DEFAULT_STOCK = 10
SUCCESS_STATUSES = {"completed", "confirmed", "succeeded", "payment-successful", "payment-safe", "payment-finalized"}
FAILED_STATUSES = {"cancelled", "failed", "rejected", "payment-failed"}
PENDING_STATUSES = {"created", "pending", "processing", "payment-included", "payment-required"}
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _ensure_fixed_primary_sku(db: Session) -> PrimaryIssuanceSku:
    sku = db.get(PrimaryIssuanceSku, PRIMARY_ISSUANCE_SKU_ID)
    if sku is not None:
        sku.display_name = PRIMARY_ISSUANCE_DISPLAY_NAME
        sku.profile_label = PRIMARY_ISSUANCE_PROFILE_LABEL
        sku.gpu_spec = PRIMARY_ISSUANCE_GPU_SPEC
        sku.model_family = PRIMARY_ISSUANCE_MODEL_FAMILY
        sku.price_cents = PRIMARY_ISSUANCE_PRICE_CENTS
        sku.currency = PRIMARY_ISSUANCE_CURRENCY
        db.add(sku)
        db.flush()
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


def _map_hsp_status(status_value: str) -> PaymentState:
    normalized = status_value.lower()
    if normalized in SUCCESS_STATUSES:
        return PaymentState.SUCCEEDED
    if normalized in FAILED_STATUSES:
        return PaymentState.FAILED
    if normalized in PENDING_STATUSES:
        return PaymentState.PENDING
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported HSP status")


def _stablecoin_smallest_units_from_cents(amount_cents: int) -> int:
    return amount_cents * 10_000


def _topic_address(topic: str | None) -> str | None:
    if topic is None:
        return None
    normalized = str(topic).strip().lower()
    if not normalized.startswith("0x") or len(normalized) < 42:
        return None
    return "0x" + normalized[-40:]


def _primary_hsp_receipt_confirms_payment(*, purchase: PrimaryIssuancePurchase, event: HSPWebhookEvent, container: Container) -> bool:
    tx_hash = _normalize_hsp_tx_hash(event.tx_hash)
    if tx_hash is None or not tx_hash.startswith("0x"):
        return False

    receipt = get_receipt_reader().get_receipt(tx_hash)
    if receipt is None or receipt.status != 1:
        return False

    token_address = container.contracts_registry.payment_token(purchase.currency.upper()).lower()
    expected_amount = _stablecoin_smallest_units_from_cents(purchase.amount_cents)
    expected_recipient = str(container.hsp_adapter.pay_to_address or "").lower()
    if not expected_recipient:
        return False

    for raw_log in receipt.metadata.get("logs", []):
        topics = [str(topic).lower() for topic in raw_log.get("topics", [])]
        if not topics or topics[0] != ERC20_TRANSFER_TOPIC:
            continue
        if str(raw_log.get("address", "")).lower() != token_address:
            continue
        if _topic_address(topics[2] if len(topics) > 2 else None) != expected_recipient:
            continue
        try:
            amount = int(str(raw_log.get("data", "0x0")), 16)
        except ValueError:
            continue
        if amount == expected_amount:
            return True
    return False


def _primary_purchase_reconciliation_from_block(
    purchase: PrimaryIssuancePurchase,
    *,
    candidate_tx_hashes: tuple[str | None, ...] = (),
) -> int | None:
    receipt_reader = get_receipt_reader()
    candidate_blocks: list[int] = []
    all_tx_hashes = (purchase.callback_tx_hash, *candidate_tx_hashes)
    for raw_tx_hash in all_tx_hashes:
        tx_hash = _normalize_hsp_tx_hash(raw_tx_hash)
        if tx_hash is None or not tx_hash.startswith("0x"):
            continue
        receipt = receipt_reader.get_receipt(tx_hash)
        if receipt is None:
            continue
        candidate_blocks.append(int(receipt.block_number))
    if not candidate_blocks:
        return None
    return min(candidate_blocks)


def _effective_primary_hsp_mapped_state(
    *,
    purchase: PrimaryIssuancePurchase,
    event: HSPWebhookEvent,
    container: Container,
) -> PaymentState:
    mapped_state = _map_hsp_status(event.status)
    if mapped_state == PaymentState.PENDING and event.status.lower() in {"payment-included", "payment-safe"}:
        if _primary_hsp_receipt_confirms_payment(purchase=purchase, event=event, container=container):
            return PaymentState.SUCCEEDED
    return mapped_state


def _query_primary_purchase_hsp_event(
    purchase: PrimaryIssuancePurchase,
    *,
    container: Container,
) -> HSPWebhookEvent | None:
    if purchase.provider != "hsp" or not container.hsp_adapter.is_live_configured:
        return None
    if purchase.provider_reference:
        return container.hsp_adapter.query_payment_status(
            payment_request_id=purchase.provider_reference,
            fallback_amount_cents=purchase.amount_cents,
            fallback_currency=purchase.currency,
        )
    if purchase.flow_id:
        return container.hsp_adapter.query_payment_status(
            flow_id=purchase.flow_id,
            fallback_amount_cents=purchase.amount_cents,
            fallback_currency=purchase.currency,
        )
    if purchase.merchant_order_id:
        return container.hsp_adapter.query_payment_status(
            cart_mandate_id=purchase.merchant_order_id,
            fallback_amount_cents=purchase.amount_cents,
            fallback_currency=purchase.currency,
        )
    return None


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


def _reserve_primary_stock_atomically(*, sku_id: str, db: Session) -> bool:
    result = db.execute(
        update(PrimaryIssuanceSku)
        .where(
            PrimaryIssuanceSku.sku_id == sku_id,
            PrimaryIssuanceSku.stock_available > 0,
        )
        .values(stock_available=PrimaryIssuanceSku.stock_available - 1)
    )
    return bool(result.rowcount)


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


def _primary_purchase_token_uri(purchase: PrimaryIssuancePurchase) -> str:
    return f"ipfs://outcomex-machine/primary-issuance/{purchase.id}"


def _ensure_primary_machine_projection(
    *,
    purchase: PrimaryIssuancePurchase,
    sku: PrimaryIssuanceSku,
    onchain_machine_id: str,
    owner_wallet: str,
    db: Session,
) -> Machine:
    machine = db.scalar(select(Machine).where(Machine.onchain_machine_id == onchain_machine_id))
    if machine is None:
        machine = Machine(
            display_name=sku.display_name,
            owner_user_id=purchase.buyer_user_id,
            owner_chain_address=owner_wallet.lower(),
            ownership_source="chain",
            onchain_machine_id=onchain_machine_id,
        )
        db.add(machine)
        db.flush()
    return machine


def _finalize_primary_purchase_success(
    *,
    purchase: PrimaryIssuancePurchase,
    sku: PrimaryIssuanceSku,
    container: Container,
    onchain_lifecycle: OnchainLifecycleService,
    db: Session,
    reconcile_existing_mint: bool,
    reconciliation_candidate_tx_hashes: tuple[str | None, ...] = (),
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

    token_uri = _primary_purchase_token_uri(purchase)
    if reconcile_existing_mint:
        reconciliation_from_block = _primary_purchase_reconciliation_from_block(
            purchase,
            candidate_tx_hashes=reconciliation_candidate_tx_hashes,
        )
        try:
            existing_onchain_machine_id = onchain_lifecycle.find_minted_machine_by_token_uri(
                token_uri=token_uri,
                from_block=reconciliation_from_block,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Primary issuance reconciliation unavailable: {exc}",
            ) from exc
        if existing_onchain_machine_id is not None:
            machine = _ensure_primary_machine_projection(
                purchase=purchase,
                sku=sku,
                onchain_machine_id=existing_onchain_machine_id,
                owner_wallet=owner_wallet,
                db=db,
            )
            purchase.minted_machine_id = machine.id
            purchase.minted_onchain_machine_id = machine.onchain_machine_id
            purchase.stock_reserved = False
            purchase.finalized_at = utc_now()
            db.add(purchase)
            return

    minted = onchain_lifecycle.mint_machine_for_owner(
        owner_user_id=purchase.buyer_user_id,
        token_uri=token_uri,
    )
    if minted.onchain_machine_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mint receipt missing machine id")

    machine = _ensure_primary_machine_projection(
        purchase=purchase,
        sku=sku,
        onchain_machine_id=minted.onchain_machine_id,
        owner_wallet=owner_wallet,
        db=db,
    )

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
    same_callback_event = purchase.callback_event_id == event.event_id
    same_callback_status = (purchase.callback_state or "").lower() == event.status.lower()
    same_callback_tx_hash = _normalize_hsp_tx_hash(purchase.callback_tx_hash) == _normalize_hsp_tx_hash(event.tx_hash)
    needs_success_reprocessing = mapped_state == PaymentState.SUCCEEDED and (
        purchase.state != PaymentState.SUCCEEDED or purchase.minted_machine_id is None
    )

    if same_callback_event and same_callback_status and same_callback_tx_hash and not needs_success_reprocessing:
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
            if purchase.minted_machine_id is not None:
                _mark_primary_purchase_callback(purchase=purchase, event=event)
                db.add(purchase)
                return {
                    "purchase_id": purchase.id,
                    "state": purchase.state.value,
                    "duplicate": True,
                    "minted_machine_id": purchase.minted_machine_id,
                    "minted_onchain_machine_id": purchase.minted_onchain_machine_id,
                }

            sku = db.get(PrimaryIssuanceSku, purchase.sku_id)
            if sku is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance SKU not found")
            previous_callback_tx_hash = purchase.callback_tx_hash
            _mark_primary_purchase_callback(purchase=purchase, event=event)
            db.add(purchase)
            _finalize_primary_purchase_success(
                purchase=purchase,
                sku=sku,
                container=container,
                onchain_lifecycle=onchain_lifecycle,
                db=db,
                reconcile_existing_mint=True,
                reconciliation_candidate_tx_hashes=(previous_callback_tx_hash, event.tx_hash),
            )
            return {
                "purchase_id": purchase.id,
                "state": purchase.state.value,
                "duplicate": False,
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
            reconcile_existing_mint=False,
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
    if not _reserve_primary_stock_atomically(sku_id=sku.sku_id, db=db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primary issuance stock exhausted")

    buyer_user_id = (
        container.buyer_address_resolver.canonicalize_user_id(payload.buyer_user_id)
        if payload.buyer_user_id
        else None
    )
    buyer_wallet = None
    if buyer_user_id:
        buyer_wallet = container.buyer_address_resolver.resolve_wallet(buyer_user_id)
        if buyer_wallet is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Buyer wallet address unresolved")
    else:
        if not payload.buyer_wallet_address:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="buyer_wallet_address is required when buyer_user_id is omitted",
            )
        buyer_user_id = container.buyer_address_resolver.resolve_user_id(payload.buyer_wallet_address)
        if buyer_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Buyer user id unresolved for wallet address",
            )
        buyer_wallet = container.buyer_address_resolver.resolve_wallet(buyer_user_id)
        if buyer_wallet is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Buyer wallet address unresolved")

    purchase = PrimaryIssuancePurchase(
        sku_id=sku.sku_id,
        buyer_user_id=buyer_user_id,
        amount_cents=sku.price_cents,
        currency=sku.currency,
        state=PaymentState.PENDING,
        stock_reserved=True,
    )
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
        _release_primary_stock_reservation_if_needed(purchase=purchase, db=db)
        purchase.state = PaymentState.FAILED
        purchase.callback_state = "intent-failed"
        purchase.callback_received_at = utc_now()
        db.add(purchase)
        db.commit()
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


@router.post(
    "/purchases/{purchase_id}/sync-hsp",
    response_model=PrimaryIssuancePurchaseSyncResponse,
)
def sync_primary_issuance_purchase_hsp(
    purchase_id: str,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> PrimaryIssuancePurchaseSyncResponse:
    purchase = db.get(PrimaryIssuancePurchase, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Primary issuance purchase not found")

    event = _query_primary_purchase_hsp_event(purchase, container=container)
    if event is None:
        return PrimaryIssuancePurchaseSyncResponse(
            purchase_id=purchase.id,
            state=purchase.state,
            remote_status=None,
            callback_event_id=purchase.callback_event_id,
            callback_tx_hash=purchase.callback_tx_hash,
            minted_machine_id=purchase.minted_machine_id,
            minted_onchain_machine_id=purchase.minted_onchain_machine_id,
            polled=False,
        )

    result = apply_primary_purchase_hsp_webhook(
        purchase=purchase,
        mapped_state=_effective_primary_hsp_mapped_state(
            purchase=purchase,
            event=event,
            container=container,
        ),
        event=event,
        container=container,
        onchain_lifecycle=onchain_lifecycle,
        db=db,
    )
    db.commit()
    db.refresh(purchase)
    return PrimaryIssuancePurchaseSyncResponse(
        purchase_id=purchase.id,
        state=purchase.state,
        remote_status=event.status,
        callback_event_id=purchase.callback_event_id,
        callback_tx_hash=purchase.callback_tx_hash,
        minted_machine_id=result.get("minted_machine_id") if isinstance(result, dict) else purchase.minted_machine_id,
        minted_onchain_machine_id=(
            result.get("minted_onchain_machine_id") if isinstance(result, dict) else purchase.minted_onchain_machine_id
        ),
        polled=True,
    )
