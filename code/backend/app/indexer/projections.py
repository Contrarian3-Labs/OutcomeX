"""Projection interfaces and in-memory read models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.indexer.events import (
    MachineAssetEvent,
    NormalizedEvent,
    OrderLifecycleEvent,
    PWRMintedEvent,
    RevenueClaimedEvent,
    SettlementSplitEvent,
    TransferGuardUpdatedEvent,
)


@dataclass(frozen=True)
class OrderView:
    order_id: str
    machine_id: str | None
    buyer: str | None
    status: str
    amount_wei: int | None
    last_event_id: str


@dataclass(frozen=True)
class MachineAssetView:
    machine_id: str
    owner: str
    metadata_uri: str | None
    pwr_quota: int | None
    last_event_id: str


@dataclass(frozen=True)
class MachineOwnershipView:
    machine_id: str
    chain_owner: str
    last_event_id: str


@dataclass(frozen=True)
class RevenueView:
    account: str
    total_claimed_wei: int
    total_settlement_wei: int
    total_pwr_minted_wei: int
    last_event_id: str


@dataclass(frozen=True)
class TransferEligibilityView:
    asset_id: str
    is_transferable: bool
    reason: str | None
    active_tasks: int | None
    unsettled_revenue: int | None
    last_event_id: str


class ProjectionStore(Protocol):
    def apply(self, event: NormalizedEvent) -> None:
        ...

    def get_order(self, order_id: str) -> OrderView:
        ...

    def get_machine_asset(self, machine_id: str) -> MachineAssetView:
        ...

    def get_machine_ownership(self, machine_id: str) -> MachineOwnershipView:
        ...

    def get_revenue(self, account: str) -> RevenueView:
        ...

    def get_transfer_eligibility(self, asset_id: str) -> TransferEligibilityView:
        ...


class InMemoryProjectionStore:
    def __init__(self) -> None:
        self._orders: dict[str, OrderView] = {}
        self._machine_assets: dict[str, MachineAssetView] = {}
        self._machine_ownership: dict[str, MachineOwnershipView] = {}
        self._revenue: dict[str, RevenueView] = {}
        self._transfer_eligibility: dict[str, TransferEligibilityView] = {}
        self.applied_event_ids: list[str] = []

    def apply(self, event: NormalizedEvent) -> None:
        self.applied_event_ids.append(event.event_id)
        payload = event.payload

        if isinstance(payload, MachineAssetEvent):
            existing = self._machine_assets.get(payload.machine_id)
            self._machine_assets[payload.machine_id] = MachineAssetView(
                machine_id=payload.machine_id,
                owner=payload.owner,
                metadata_uri=(
                    payload.metadata_uri
                    if payload.metadata_uri is not None
                    else (existing.metadata_uri if existing is not None else None)
                ),
                pwr_quota=(
                    payload.pwr_quota
                    if payload.pwr_quota is not None
                    else (existing.pwr_quota if existing is not None else None)
                ),
                last_event_id=event.event_id,
            )
            self._machine_ownership[payload.machine_id] = MachineOwnershipView(
                machine_id=payload.machine_id,
                chain_owner=payload.owner,
                last_event_id=event.event_id,
            )
        elif isinstance(payload, OrderLifecycleEvent):
            existing = self._orders.get(payload.order_id)
            self._orders[payload.order_id] = OrderView(
                order_id=payload.order_id,
                machine_id=(
                    payload.machine_id
                    if payload.machine_id is not None
                    else (existing.machine_id if existing is not None else None)
                ),
                buyer=(
                    payload.buyer
                    if payload.buyer is not None
                    else (existing.buyer if existing is not None else None)
                ),
                status=payload.status,
                amount_wei=(
                    payload.amount_wei
                    if payload.amount_wei is not None
                    else (existing.amount_wei if existing is not None else None)
                ),
                last_event_id=event.event_id,
            )
        elif isinstance(payload, SettlementSplitEvent):
            self._upsert_revenue(
                account=payload.recipient,
                claimed_delta=0,
                settlement_delta=payload.amount_wei,
                pwr_delta=0,
                event_id=event.event_id,
            )
        elif isinstance(payload, RevenueClaimedEvent):
            self._upsert_revenue(
                account=payload.account,
                claimed_delta=payload.amount_wei,
                settlement_delta=0,
                pwr_delta=0,
                event_id=event.event_id,
            )
        elif isinstance(payload, TransferGuardUpdatedEvent):
            self._transfer_eligibility[payload.asset_id] = TransferEligibilityView(
                asset_id=payload.asset_id,
                is_transferable=payload.is_transferable,
                reason=payload.reason,
                active_tasks=payload.active_tasks,
                unsettled_revenue=payload.unsettled_revenue,
                last_event_id=event.event_id,
            )
        elif isinstance(payload, PWRMintedEvent):
            self._upsert_revenue(
                account=payload.account,
                claimed_delta=0,
                settlement_delta=0,
                pwr_delta=payload.amount_wei,
                event_id=event.event_id,
            )

    def _upsert_revenue(
        self,
        *,
        account: str,
        claimed_delta: int,
        settlement_delta: int,
        pwr_delta: int,
        event_id: str,
    ) -> None:
        existing = self._revenue.get(account)
        if existing is None:
            existing = RevenueView(
                account=account,
                total_claimed_wei=0,
                total_settlement_wei=0,
                total_pwr_minted_wei=0,
                last_event_id=event_id,
            )
        self._revenue[account] = RevenueView(
            account=account,
            total_claimed_wei=existing.total_claimed_wei + claimed_delta,
            total_settlement_wei=existing.total_settlement_wei + settlement_delta,
            total_pwr_minted_wei=existing.total_pwr_minted_wei + pwr_delta,
            last_event_id=event_id,
        )

    def get_order(self, order_id: str) -> OrderView:
        return self._orders[order_id]

    def get_machine_asset(self, machine_id: str) -> MachineAssetView:
        return self._machine_assets[machine_id]

    def get_machine_ownership(self, machine_id: str) -> MachineOwnershipView:
        return self._machine_ownership[machine_id]

    def get_revenue(self, account: str) -> RevenueView:
        return self._revenue[account]

    def get_transfer_eligibility(self, asset_id: str) -> TransferEligibilityView:
        return self._transfer_eligibility[asset_id]
