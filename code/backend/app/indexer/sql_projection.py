"""SQLAlchemy-backed projection store for live indexer updates."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import (
    Machine,
    MachineListing,
    MachineRevenueClaim,
    Order,
    Payment,
    RevenueEntry,
    SettlementClaimRecord,
    SettlementRecord,
    utc_now,
)
from app.domain.pwr_amounts import parse_pwr_wei, pwr_wei_to_cents
from app.domain.order_truth import set_authoritative_order_truth
from app.domain.rules import (
    calculate_failed_or_no_valid_preview_breakdown,
    calculate_rejected_valid_preview_breakdown,
)
from app.domain.settlement_projection import ensure_confirmed_settlement_projection, ensure_settlement_projection
from app.indexer.events import (
    MachineAssetEvent,
    MarketplaceListingEvent,
    NormalizedEvent,
    OrderLifecycleEvent,
    RevenueClaimedEvent,
    SettlementSplitEvent,
)
from app.indexer.projections import InMemoryProjectionStore
from app.indexer.recovery import (
    fallback_machine_display_name,
    payment_provider_from_source,
    placeholder_chat_session_id,
    placeholder_plan_summary,
    placeholder_user_prompt,
    projection_uuid,
    resolve_projected_user_id,
)
from app.integrations.machine_ownership_projection import MachineOwnershipProjectionIntegrator


def _timestamp_to_datetime(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _payment_token_symbol(address: str | None) -> str | None:
    if address is None:
        return None
    settings = get_settings()
    normalized = address.lower()
    if normalized == settings.onchain_usdc_address.lower():
        return "USDC"
    if normalized == settings.onchain_usdt_address.lower():
        return "USDT"
    if normalized == settings.onchain_pwr_token_address.lower():
        return "PWR"
    return None


def _payment_token_decimals(address: str | None) -> int | None:
    symbol = _payment_token_symbol(address)
    if symbol in {"USDC", "USDT"}:
        return 6
    if symbol == "PWR":
        return 18
    return None


def _claim_amount_to_cents(payload: RevenueClaimedEvent) -> int:
    if payload.claim_kind == "machine_revenue":
        return pwr_wei_to_cents(payload.amount_wei)

    symbol = _payment_token_symbol(payload.token_address)
    if symbol == "PWR":
        return pwr_wei_to_cents(payload.amount_wei)

    decimals = _payment_token_decimals(payload.token_address)
    if symbol in {"USDC", "USDT"} and decimals is not None:
        amount_cents = (Decimal(payload.amount_wei) * Decimal(100)) / (Decimal(10) ** decimals)
        return int(amount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return payload.amount_wei


def _amount_to_cents(*, amount: int | None, token_address: str | None) -> int:
    if amount is None:
        return 0
    symbol = _payment_token_symbol(token_address)
    if symbol == "PWR":
        return pwr_wei_to_cents(amount)
    decimals = _payment_token_decimals(token_address)
    if symbol in {"USDC", "USDT"} and decimals is not None:
        amount_cents = (Decimal(amount) * Decimal(100)) / (Decimal(10) ** decimals)
        return int(amount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return amount


def _reconstructed_order_metadata(*, event: NormalizedEvent, payload: OrderLifecycleEvent) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "reconstructed_from_chain": True,
        "reconstructed_missing_fields": [
            "chat_session_id",
            "user_prompt",
            "recommended_plan_summary",
            "execution_request",
            "execution_plan",
        ],
        "reconstructed_from_event_id": event.event_id,
        "reconstructed_from_event_name": event.event_name,
    }
    if payload.amount_wei is not None:
        metadata["reconstructed_onchain_gross_amount"] = str(payload.amount_wei)
    if payload.payment_token is not None:
        metadata["reconstructed_payment_token"] = payload.payment_token
    if payload.payment_source is not None:
        metadata["reconstructed_payment_source"] = payload.payment_source
    return metadata


class SqlProjectionStore:
    """Apply indexed events to backend DB while retaining in-memory read models."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        owner_resolver=None,
    ) -> None:
        self._session_factory = session_factory
        self._mirror = InMemoryProjectionStore()
        self._ownership_integrator = MachineOwnershipProjectionIntegrator(owner_resolver=owner_resolver)
        self._user_resolver = owner_resolver

    def apply(self, event: NormalizedEvent) -> None:
        self._mirror.apply(event)
        payload = event.payload
        if isinstance(payload, MachineAssetEvent):
            self._apply_machine_event(machine_id=payload.machine_id, chain_owner=payload.owner, event_id=event.event_id)
            return
        if isinstance(payload, MarketplaceListingEvent):
            self._apply_marketplace_listing_event(event=event, payload=payload)
            return
        if isinstance(payload, OrderLifecycleEvent):
            self._apply_order_event(event=event, payload=payload)
            return
        if isinstance(payload, SettlementSplitEvent):
            self._apply_settlement_split(payload=payload)
            return
        if isinstance(payload, RevenueClaimedEvent):
            self._apply_revenue_claim(event=event, payload=payload)

    def get_order(self, order_id: str):
        return self._mirror.get_order(order_id)

    def get_machine_asset(self, machine_id: str):
        return self._mirror.get_machine_asset(machine_id)

    def get_machine_ownership(self, machine_id: str):
        return self._mirror.get_machine_ownership(machine_id)

    def get_revenue(self, account: str):
        return self._mirror.get_revenue(account)

    def get_transfer_eligibility(self, asset_id: str):
        return self._mirror.get_transfer_eligibility(asset_id)

    def _resolve_user_id(self, chain_address: str | None, *, fallback_prefix: str, natural_key: str | None = None) -> str:
        return resolve_projected_user_id(
            self._user_resolver,
            chain_address,
            fallback_prefix=fallback_prefix,
            natural_key=natural_key,
        )

    def _ensure_machine_projection(
        self,
        *,
        db,
        onchain_machine_id: str | None,
        owner_chain_address: str | None,
        metadata_uri: str | None = None,
        event_id: str | None = None,
    ) -> Machine | None:
        if onchain_machine_id is None:
            return None

        machine = db.scalar(
            select(Machine).where(
                (Machine.onchain_machine_id == onchain_machine_id) | (Machine.id == onchain_machine_id),
            )
        )
        if machine is None:
            machine = Machine(
                id=projection_uuid("machine", onchain_machine_id),
                onchain_machine_id=onchain_machine_id,
                display_name=fallback_machine_display_name(onchain_machine_id, metadata_uri=metadata_uri),
                owner_user_id=self._resolve_user_id(
                    owner_chain_address,
                    fallback_prefix="machine-owner",
                    natural_key=onchain_machine_id,
                ),
                owner_chain_address=owner_chain_address.lower() if owner_chain_address else None,
                ownership_source="chain",
            )
        elif not machine.onchain_machine_id:
            machine.onchain_machine_id = onchain_machine_id

        if owner_chain_address:
            machine.owner_chain_address = owner_chain_address.lower()
            machine.owner_user_id = self._resolve_user_id(
                owner_chain_address,
                fallback_prefix="machine-owner",
                natural_key=onchain_machine_id,
            )
            machine.ownership_source = "chain"
            machine.owner_projected_at = utc_now()
            machine.owner_projection_last_event_id = event_id

        db.add(machine)
        db.flush()

        for listing in db.scalars(
            select(MachineListing).where(
                MachineListing.onchain_machine_id == onchain_machine_id,
                MachineListing.machine_id.is_(None),
            )
        ):
            listing.machine_id = machine.id
            db.add(listing)

        return machine

    def _reconstruct_order(
        self,
        *,
        db,
        event: NormalizedEvent,
        payload: OrderLifecycleEvent,
    ) -> Order | None:
        machine = self._ensure_machine_projection(
            db=db,
            onchain_machine_id=payload.machine_id,
            owner_chain_address=payload.settlement_beneficiary,
            event_id=event.event_id,
        )
        if machine is None:
            return None

        quoted_amount_cents = 0
        if payload.payment_token is not None:
            quoted_amount_cents = _amount_to_cents(amount=payload.amount_wei, token_address=payload.payment_token)
        elif event.event_name == "OrderCreated" and payload.amount_wei is not None:
            quoted_amount_cents = payload.amount_wei

        buyer_user_id = self._resolve_user_id(
            payload.buyer,
            fallback_prefix="buyer",
            natural_key=payload.order_id,
        )
        beneficiary_user_id = (
            self._resolve_user_id(
                payload.settlement_beneficiary,
                fallback_prefix="beneficiary",
                natural_key=payload.order_id,
            )
            if payload.settlement_beneficiary is not None
            else machine.owner_user_id
        )
        order = Order(
            id=projection_uuid("order", payload.order_id),
            onchain_order_id=payload.order_id,
            onchain_machine_id=payload.machine_id,
            create_order_tx_hash=event.transaction_hash if payload.status.upper() in {"CREATED", "PAID"} else None,
            create_order_event_id=event.event_id if payload.status.upper() == "CREATED" else None,
            create_order_block_number=event.block_number if payload.status.upper() in {"CREATED", "PAID"} else None,
            user_id=buyer_user_id,
            machine_id=machine.id,
            chat_session_id=placeholder_chat_session_id(payload.order_id),
            user_prompt=placeholder_user_prompt(payload.order_id),
            recommended_plan_summary=placeholder_plan_summary(payload.order_id),
            quoted_amount_cents=quoted_amount_cents,
            state=OrderState.PLAN_RECOMMENDED,
            execution_state=ExecutionState.QUEUED,
            preview_state=PreviewState.DRAFT,
            settlement_state=SettlementState.NOT_READY,
            settlement_beneficiary_user_id=beneficiary_user_id,
            settlement_is_self_use=buyer_user_id == beneficiary_user_id,
            settlement_is_dividend_eligible=(
                payload.dividend_eligible
                if payload.dividend_eligible is not None
                else buyer_user_id != beneficiary_user_id
            ),
            execution_metadata=_reconstructed_order_metadata(event=event, payload=payload),
        )
        db.add(order)
        db.flush()
        return order

    def _ensure_order_projection(
        self,
        *,
        db,
        event: NormalizedEvent,
        payload: OrderLifecycleEvent,
    ) -> Order | None:
        order = db.scalar(select(Order).where(Order.onchain_order_id == payload.order_id))
        if order is not None:
            current_machine = db.get(Machine, order.machine_id) if order.machine_id else None
            if payload.machine_id and not order.onchain_machine_id:
                order.onchain_machine_id = payload.machine_id
            if current_machine is not None:
                if payload.machine_id and not current_machine.onchain_machine_id:
                    current_machine.onchain_machine_id = payload.machine_id
                if payload.settlement_beneficiary and not current_machine.owner_chain_address:
                    current_machine.owner_chain_address = payload.settlement_beneficiary.lower()
                    current_machine.ownership_source = "chain"
                    current_machine.owner_projection_last_event_id = event.event_id
                    current_machine.owner_projected_at = utc_now()
                db.add(current_machine)
            elif payload.machine_id:
                machine = self._ensure_machine_projection(
                    db=db,
                    onchain_machine_id=payload.machine_id,
                    owner_chain_address=payload.settlement_beneficiary,
                    event_id=event.event_id,
                )
                if machine is not None:
                    order.machine_id = machine.id
            if payload.amount_wei is not None and payload.payment_token is not None and order.quoted_amount_cents <= 0:
                order.quoted_amount_cents = _amount_to_cents(
                    amount=payload.amount_wei,
                    token_address=payload.payment_token,
                )
            if payload.settlement_beneficiary and not order.settlement_beneficiary_user_id:
                beneficiary_user_id = current_machine.owner_user_id if current_machine is not None else self._resolve_user_id(
                    payload.settlement_beneficiary,
                    fallback_prefix="beneficiary",
                    natural_key=payload.order_id,
                )
                order.settlement_beneficiary_user_id = beneficiary_user_id
                order.settlement_is_self_use = order.user_id == beneficiary_user_id
                order.settlement_is_dividend_eligible = order.user_id != beneficiary_user_id
            db.add(order)
            db.flush()
            return order

        if payload.status.upper() in {"CREATED", "PAID"}:
            order = db.scalar(
                select(Order).where(
                    Order.create_order_tx_hash == event.transaction_hash,
                )
            )
            if order is not None:
                order.onchain_order_id = payload.order_id
                order.onchain_machine_id = payload.machine_id or order.onchain_machine_id
                if payload.status.upper() == "CREATED" or order.create_order_event_id is None:
                    order.create_order_event_id = event.event_id
                    order.create_order_block_number = event.block_number
                db.add(order)
                db.flush()
                return order

        return self._reconstruct_order(db=db, event=event, payload=payload)

    @staticmethod
    def _reconstructed_payment_id(*, order_id: str, transaction_hash: str, provider: str) -> str:
        return projection_uuid("payment", f"{order_id}:{transaction_hash}:{provider}")

    def _ensure_paid_payment_projection(
        self,
        *,
        db,
        order: Order,
        event: NormalizedEvent,
        payload: OrderLifecycleEvent,
    ) -> Payment | None:
        payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.callback_tx_hash == event.transaction_hash,
            )
            .order_by(Payment.created_at.desc())
        )
        provider = payment_provider_from_source(payload.payment_source)
        currency = _payment_token_symbol(payload.payment_token) or order.latest_success_payment_currency or "USD"
        amount_cents = _amount_to_cents(amount=payload.amount_wei, token_address=payload.payment_token)
        if payment is None:
            payment = db.scalar(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.provider == provider,
                    Payment.state.in_((PaymentState.PENDING, PaymentState.SUCCEEDED)),
                )
                .order_by(Payment.created_at.desc())
            )
        if payment is None:
            payment = Payment(
                id=self._reconstructed_payment_id(order_id=order.id, transaction_hash=event.transaction_hash, provider=provider),
                order_id=order.id,
                provider=provider,
                provider_reference=payload.payment_source,
                merchant_order_id=(order.id if provider == "onchain_router" else None),
                flow_id=None,
                checkout_url=None,
                provider_payload={
                    "reconstructed_from_chain": True,
                    "payment_source": payload.payment_source,
                    "payer": payload.payer,
                    "payment_token": payload.payment_token,
                },
                amount_cents=amount_cents if amount_cents > 0 else order.quoted_amount_cents,
                currency=currency,
                state=PaymentState.SUCCEEDED,
            )
        else:
            if amount_cents > 0:
                payment.amount_cents = amount_cents
            payment.currency = currency
            payment.provider = provider

        payment.callback_state = PaymentState.SUCCEEDED.value
        payment.callback_tx_hash = event.transaction_hash
        payment.callback_event_id = event.event_id
        payment.callback_received_at = utc_now()
        payment.state = PaymentState.SUCCEEDED
        db.add(payment)
        db.flush()
        return payment

    def _apply_machine_event(self, *, machine_id: str, chain_owner: str, event_id: str) -> None:
        with self._session_factory() as db:
            self._ownership_integrator.apply_machine_owner_projection(
                db=db,
                machine_id=machine_id,
                chain_owner=chain_owner,
                event_id=event_id,
            )

    def _apply_marketplace_listing_event(self, *, event: NormalizedEvent, payload: MarketplaceListingEvent) -> None:
        with self._session_factory() as db:
            listing = db.scalar(
                select(MachineListing).where(
                    MachineListing.onchain_listing_id == payload.listing_id,
                )
            )
            machine = self._ensure_machine_projection(
                db=db,
                onchain_machine_id=payload.machine_id,
                owner_chain_address=payload.seller,
                event_id=event.event_id,
            )

            now = datetime.now(timezone.utc)
            if listing is None:
                if payload.status != "ACTIVE":
                    return
                if payload.seller is None or payload.payment_token is None or payload.price_wei is None:
                    return
                listing = MachineListing(
                    onchain_listing_id=payload.listing_id,
                    machine_id=machine.id if machine is not None else None,
                    onchain_machine_id=payload.machine_id,
                    seller_chain_address=payload.seller,
                    buyer_chain_address=payload.buyer,
                    payment_token_address=payload.payment_token,
                    payment_token_symbol=_payment_token_symbol(payload.payment_token),
                    payment_token_decimals=_payment_token_decimals(payload.payment_token),
                    price_units=payload.price_wei,
                    state="active",
                    listed_tx_hash=event.transaction_hash,
                    expires_at=_timestamp_to_datetime(payload.expiry_timestamp),
                    listed_at=now,
                    last_event_id=event.event_id,
                )
                db.add(listing)
                db.commit()
                return

            if machine is not None and listing.machine_id is None:
                listing.machine_id = machine.id
            if payload.machine_id is not None:
                listing.onchain_machine_id = payload.machine_id
            if payload.seller is not None:
                listing.seller_chain_address = payload.seller
            if payload.buyer is not None:
                listing.buyer_chain_address = payload.buyer
            if payload.payment_token is not None:
                listing.payment_token_address = payload.payment_token
                listing.payment_token_symbol = _payment_token_symbol(payload.payment_token)
                listing.payment_token_decimals = _payment_token_decimals(payload.payment_token)
            if payload.price_wei is not None:
                listing.price_units = payload.price_wei
            if payload.expiry_timestamp is not None:
                listing.expires_at = _timestamp_to_datetime(payload.expiry_timestamp)

            if payload.status == "ACTIVE":
                listing.state = "active"
                listing.listed_tx_hash = event.transaction_hash
                listing.cancel_tx_hash = None
                listing.filled_tx_hash = None
                listing.cancelled_at = None
                listing.filled_at = None
            elif payload.status == "CANCELLED":
                listing.state = "cancelled"
                listing.cancel_tx_hash = event.transaction_hash
                listing.cancelled_at = now
            elif payload.status == "FILLED":
                listing.state = "filled"
                listing.filled_tx_hash = event.transaction_hash
                listing.filled_at = now

            listing.last_event_id = event.event_id
            db.add(listing)
            db.commit()

    def _apply_order_event(self, *, event: NormalizedEvent, payload: OrderLifecycleEvent) -> None:
        with self._session_factory() as db:
            order = self._ensure_order_projection(db=db, event=event, payload=payload)
            if order is None:
                return

            machine = db.get(Machine, order.machine_id)
            order_status = payload.status.upper()
            if order_status != "CLASSIFIED":
                self._project_authoritative_order_truth(order=order, event=event, order_status=order_status, payload=payload)
            if order_status == "PAID" and machine is not None:
                payment = self._ensure_paid_payment_projection(db=db, order=order, event=event, payload=payload)
                if payment is not None and order.quoted_amount_cents <= 0:
                    order.quoted_amount_cents = payment.amount_cents
                if payload.settlement_beneficiary is not None:
                    beneficiary_user_id = machine.owner_user_id or self._resolve_user_id(
                        payload.settlement_beneficiary,
                        fallback_prefix="beneficiary",
                        natural_key=payload.order_id,
                    )
                    order.settlement_beneficiary_user_id = beneficiary_user_id
                    order.settlement_is_self_use = order.user_id == beneficiary_user_id
                    order.settlement_is_dividend_eligible = (
                        payload.dividend_eligible
                        if payload.dividend_eligible is not None
                        else order.user_id != beneficiary_user_id
                    )
                self._freeze_settlement_policy_if_fully_paid(db=db, order=order, machine=machine)
                machine.has_active_tasks = True
                db.add(machine)
                if order.state == OrderState.PLAN_RECOMMENDED:
                    order.state = OrderState.USER_CONFIRMED
            elif order_status == "CANCELLED":
                if machine is not None:
                    machine.has_active_tasks = False
                    db.add(machine)
                order.state = OrderState.CANCELLED
                if payload.cancelled_at is not None:
                    order.cancelled_at = datetime.fromtimestamp(payload.cancelled_at, tz=timezone.utc)
                else:
                    order.cancelled_at = order.cancelled_at or datetime.now(timezone.utc)
                metadata = dict(order.execution_metadata or {})
                if payload.cancelled_as_expired is not None:
                    metadata["cancelled_as_expired"] = payload.cancelled_as_expired
                    if payload.cancelled_as_expired:
                        order.preview_state = PreviewState.EXPIRED
                order.execution_metadata = metadata
            elif order_status == "PREVIEW_READY":
                order.preview_state = PreviewState.READY
                if order.state in {OrderState.PLAN_RECOMMENDED, OrderState.USER_CONFIRMED, OrderState.EXECUTING}:
                    order.state = OrderState.RESULT_PENDING_CONFIRMATION
            elif order_status in {"CONFIRMED", "REJECTED", "REFUNDED"}:
                if machine is not None:
                    machine.has_active_tasks = False
                    db.add(machine)
                if order_status == "CONFIRMED":
                    order.state = OrderState.RESULT_CONFIRMED
                    order.result_confirmed_at = order.result_confirmed_at or datetime.now(timezone.utc)
                    paid_cents = db.scalar(
                        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                            Payment.order_id == order.id,
                            Payment.state == PaymentState.SUCCEEDED,
                        )
                    )
                    gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
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
                elif order_status == "REJECTED":
                    order.state = OrderState.CANCELLED
                    paid_cents = db.scalar(
                        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                            Payment.order_id == order.id,
                            Payment.state == PaymentState.SUCCEEDED,
                        )
                    )
                    gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
                    if machine is not None:
                        breakdown = calculate_rejected_valid_preview_breakdown(gross_amount_cents)
                        settlement, entry = ensure_settlement_projection(
                            db=db,
                            order=order,
                            machine=machine,
                            gross_amount_cents=breakdown.gross_amount_cents,
                            platform_fee_cents=breakdown.platform_fee_cents,
                            machine_share_cents=breakdown.machine_share_cents,
                            distributed_at=datetime.now(timezone.utc),
                        )
                        settlement.state = SettlementState.DISTRIBUTED
                        db.add(settlement)
                        db.add(entry)
                elif order_status == "REFUNDED":
                    order.state = OrderState.CANCELLED
                    paid_cents = db.scalar(
                        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                            Payment.order_id == order.id,
                            Payment.state == PaymentState.SUCCEEDED,
                        )
                    )
                    gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
                    if machine is not None:
                        breakdown = calculate_failed_or_no_valid_preview_breakdown(gross_amount_cents)
                        settlement, entry = ensure_settlement_projection(
                            db=db,
                            order=order,
                            machine=machine,
                            gross_amount_cents=breakdown.gross_amount_cents,
                            platform_fee_cents=breakdown.platform_fee_cents,
                            machine_share_cents=breakdown.machine_share_cents,
                            distributed_at=datetime.now(timezone.utc),
                        )
                        settlement.state = SettlementState.DISTRIBUTED
                        db.add(settlement)
                        db.add(entry)
                else:
                    order.state = OrderState.CANCELLED
                order.settlement_state = SettlementState.DISTRIBUTED
            db.add(order)
            db.commit()

    @staticmethod
    def _project_authoritative_order_truth(
        *,
        order: Order,
        event: NormalizedEvent,
        order_status: str,
        payload: OrderLifecycleEvent,
    ) -> None:
        set_authoritative_order_truth(
            order,
            order_status=order_status,
            event_id=event.event_id,
            cancelled_as_expired=payload.cancelled_as_expired,
        )

    @staticmethod
    def _freeze_settlement_policy_if_fully_paid(*, db, order: Order, machine: Machine) -> None:
        paid_cents = db.scalar(
            select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                Payment.order_id == order.id,
                Payment.state == PaymentState.SUCCEEDED,
            )
        )
        gross_amount_cents = effective_paid_amount_cents(order=order, paid_amount_cents=paid_cents)
        if gross_amount_cents < order.quoted_amount_cents:
            return
        if order.settlement_beneficiary_user_id is None:
            order.settlement_beneficiary_user_id = machine.owner_user_id
            order.settlement_is_self_use = order.user_id == machine.owner_user_id
            order.settlement_is_dividend_eligible = order.user_id != machine.owner_user_id
        db.add(order)

    @staticmethod
    def _mark_direct_payment_refunded(*, db, order: Order) -> None:
        payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.provider == "onchain_router",
                Payment.state == PaymentState.SUCCEEDED,
            )
            .order_by(Payment.created_at.desc())
        )
        if payment is None:
            return
        payment.state = PaymentState.REFUNDED
        payment.callback_state = PaymentState.REFUNDED.value
        payment.callback_received_at = datetime.now(timezone.utc)
        db.add(payment)

    def _apply_settlement_split(self, *, payload: SettlementSplitEvent) -> None:
        with self._session_factory() as db:
            order = db.scalar(
                select(Order).where(
                    Order.onchain_order_id == payload.order_id,
                )
            )
            if order is None:
                return
            machine = db.get(Machine, order.machine_id)
            if machine is None:
                return
            entry = db.scalar(select(RevenueEntry).where(RevenueEntry.order_id == order.id))
            if entry is not None and payload.role == "MACHINE_OWNER_DIVIDEND":
                entry.machine_share_pwr_wei = str(payload.amount_wei)
                db.add(entry)
            machine.has_unsettled_revenue = payload.amount_wei > 0
            db.add(machine)
            db.commit()

    def _apply_revenue_claim(self, *, event: NormalizedEvent, payload: RevenueClaimedEvent) -> None:
        with self._session_factory() as db:
            machine = self._ensure_machine_projection(
                db=db,
                onchain_machine_id=payload.machine_id,
                owner_chain_address=payload.account if payload.claim_kind == "machine_revenue" else None,
                event_id=event.event_id,
            )
            existing_claim_record = db.scalar(
                select(SettlementClaimRecord).where(
                    SettlementClaimRecord.event_id == event.event_id,
                )
            )
            if existing_claim_record is None:
                claimant_user_id = self._resolve_user_id(
                    payload.account,
                    fallback_prefix="claimant",
                    natural_key=event.event_id,
                )
                amount_cents = _claim_amount_to_cents(payload)
                db.add(
                    SettlementClaimRecord(
                        event_id=event.event_id,
                        claim_kind=payload.claim_kind,
                        claimant_user_id=claimant_user_id,
                        account_address=payload.account,
                        token_address=payload.token_address,
                        amount_cents=amount_cents,
                        amount_wei=str(payload.amount_wei),
                        tx_hash=event.transaction_hash,
                        machine_id=machine.id if machine is not None else None,
                    )
                )

            if payload.claim_kind != "machine_revenue" or machine is None:
                db.commit()
                return

            existing_claim = db.scalar(
                select(MachineRevenueClaim).where(
                    MachineRevenueClaim.machine_id == machine.id,
                    MachineRevenueClaim.tx_hash == event.transaction_hash,
                )
            )
            if existing_claim is None:
                db.add(
                    MachineRevenueClaim(
                        machine_id=machine.id,
                        amount_cents=_claim_amount_to_cents(payload),
                        amount_wei=str(payload.amount_wei),
                        tx_hash=event.transaction_hash,
                    )
                )
            machine.has_unsettled_revenue = (
                payload.remaining_unsettled_wei > 0
                if payload.remaining_unsettled_wei is not None
                else False
            )
            db.add(machine)
            db.commit()
