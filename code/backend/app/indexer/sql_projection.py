"""SQLAlchemy-backed projection store for live indexer updates."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, MachineRevenueClaim, Order, Payment, RevenueEntry, SettlementClaimRecord, SettlementRecord
from app.domain.order_truth import set_authoritative_order_truth
from app.domain.rules import (
    calculate_failed_or_no_valid_preview_breakdown,
    calculate_rejected_valid_preview_breakdown,
)
from app.domain.settlement_projection import ensure_confirmed_settlement_projection, ensure_settlement_projection
from app.indexer.events import (
    MachineAssetEvent,
    NormalizedEvent,
    OrderLifecycleEvent,
    RevenueClaimedEvent,
    SettlementSplitEvent,
)
from app.indexer.projections import InMemoryProjectionStore
from app.integrations.machine_ownership_projection import MachineOwnershipProjectionIntegrator


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

    def _apply_machine_event(self, *, machine_id: str, chain_owner: str, event_id: str) -> None:
        with self._session_factory() as db:
            self._ownership_integrator.apply_machine_owner_projection(
                db=db,
                machine_id=machine_id,
                chain_owner=chain_owner,
                event_id=event_id,
            )

    def _apply_order_event(self, *, event: NormalizedEvent, payload: OrderLifecycleEvent) -> None:
        with self._session_factory() as db:
            order = self._resolve_order_for_event(db=db, event=event, payload=payload)
            if order is None:
                return

            machine = db.get(Machine, order.machine_id)
            order_status = payload.status.upper()
            self._project_authoritative_order_truth(order=order, event=event, order_status=order_status, payload=payload)
            if order_status == "PAID" and machine is not None:
                self._mark_direct_payment_succeeded(db=db, order=order, tx_hash=event.transaction_hash)
                self._freeze_settlement_policy_if_fully_paid(db=db, order=order, machine=machine)
                machine.has_active_tasks = True
                db.add(machine)
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
                if order.state == OrderState.EXECUTING:
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
                    self._mark_direct_payment_refunded(db=db, order=order)
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
    def _resolve_order_for_event(*, db, event: NormalizedEvent, payload: OrderLifecycleEvent) -> Order | None:
        order = db.scalar(
            select(Order).where(
                Order.onchain_order_id == payload.order_id,
            )
        )
        if order is not None:
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
        return order

    @staticmethod
    def _mark_direct_payment_succeeded(*, db, order: Order, tx_hash: str) -> None:
        payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.provider == "onchain_router",
                Payment.callback_tx_hash == tx_hash,
            )
            .order_by(Payment.created_at.desc())
        )
        if payment is None:
            payment = db.scalar(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.provider == "onchain_router",
                    Payment.state == PaymentState.PENDING,
                )
                .order_by(Payment.created_at.desc())
            )
        if payment is None or payment.state == PaymentState.SUCCEEDED:
            return

        payment.state = PaymentState.SUCCEEDED
        payment.callback_state = PaymentState.SUCCEEDED.value
        payment.callback_tx_hash = tx_hash
        payment.callback_received_at = datetime.now(timezone.utc)
        db.add(payment)
        db.flush()

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
            machine.has_unsettled_revenue = payload.amount_wei > 0
            db.add(machine)
            db.commit()

    def _apply_revenue_claim(self, *, event: NormalizedEvent, payload: RevenueClaimedEvent) -> None:
        with self._session_factory() as db:
            machine = (
                db.scalar(select(Machine).where(Machine.onchain_machine_id == payload.machine_id))
                if payload.machine_id is not None
                else None
            )
            existing_claim_record = db.scalar(
                select(SettlementClaimRecord).where(
                    SettlementClaimRecord.event_id == event.event_id,
                )
            )
            if existing_claim_record is None:
                claimant_user_id = self._user_resolver(payload.account) if self._user_resolver is not None else None
                db.add(
                    SettlementClaimRecord(
                        event_id=event.event_id,
                        claim_kind=payload.claim_kind,
                        claimant_user_id=claimant_user_id,
                        account_address=payload.account,
                        token_address=payload.token_address,
                        amount_cents=payload.amount_wei,
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
                        amount_cents=payload.amount_wei,
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
