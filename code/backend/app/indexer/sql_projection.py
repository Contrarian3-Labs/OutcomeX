"""SQLAlchemy-backed projection store for live indexer updates."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.accounting import effective_paid_amount_cents
from app.domain.enums import OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Machine, Order, Payment, RevenueEntry, SettlementRecord
from app.domain.settlement_projection import ensure_confirmed_settlement_projection
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
            self._apply_revenue_claim(payload=payload)

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
            order = db.scalar(
                select(Order).where(
                    Order.onchain_order_id == payload.order_id,
                )
            )
            if order is None and payload.status.upper() == "CREATED":
                order = db.scalar(
                    select(Order).where(
                        Order.create_order_tx_hash == event.transaction_hash,
                    )
                )
                if order is not None:
                    order.onchain_order_id = payload.order_id
                    order.onchain_machine_id = payload.machine_id or order.onchain_machine_id
                    db.add(order)
            if order is None:
                return

            machine = db.get(Machine, order.machine_id)
            order_status = payload.status.upper()
            if order_status == "PAID" and machine is not None:
                machine.has_active_tasks = True
                db.add(machine)
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
                else:
                    order.state = OrderState.CANCELLED
                order.settlement_state = SettlementState.DISTRIBUTED
            db.add(order)
            db.commit()

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

    def _apply_revenue_claim(self, *, payload: RevenueClaimedEvent) -> None:
        if payload.machine_id is None:
            return
        with self._session_factory() as db:
            machine = db.scalar(select(Machine).where(Machine.onchain_machine_id == payload.machine_id))
            if machine is None:
                return
            machine.has_unsettled_revenue = False
            db.add(machine)
            db.commit()
