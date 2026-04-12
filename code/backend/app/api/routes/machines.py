from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.container import get_container
from app.domain.models import Machine, MachineListing, MachineRevenueClaim, RevenueEntry
from app.domain.pwr_amounts import pwr_wei_to_float
from app.domain.revenue_amounts import machine_revenue_claim_amount_wei, revenue_entry_machine_share_wei
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.runtime.hardware_simulator import get_shared_hardware_simulator
from app.schemas.machine import (
    MachineCreateRequest,
    MachineListingSummaryResponse,
    MachineResponse,
    MachineRuntimeSnapshotResponse,
)

router = APIRouter()
MACHINE_ASSET_COST_CENTS = 399_900
MOCK_MACHINE_SPEC = {
    "profile_label": "Qwen Family",
    "gpu_spec": "Apple Silicon 96GB Unified Memory",
    "supported_categories": [
        "image_generation",
        "video_generation",
        "text_reasoning",
        "multimodal",
        "agentic_workflows",
    ],
    "hosted_by": "OutcomeX Hosted Rack",
}


def _transfer_blocking_reasons(machine: Machine) -> list[str]:
    reasons: list[str] = []
    if machine.has_active_tasks:
        reasons.append("active_tasks")
    if machine.has_unsettled_revenue:
        reasons.append("unsettled_revenue")
    return reasons


def _machine_revenue_summary(*, machine_ids: list[str], db: Session) -> dict[str, dict[str, int]]:
    if not machine_ids:
        return {}

    projected_rows = db.execute(
        select(
            RevenueEntry.machine_id,
            func.coalesce(func.sum(RevenueEntry.machine_share_cents), 0),
        )
        .where(RevenueEntry.machine_id.in_(machine_ids))
        .group_by(RevenueEntry.machine_id)
    ).all()
    claimed_rows = db.execute(
        select(
            MachineRevenueClaim.machine_id,
            func.coalesce(func.sum(MachineRevenueClaim.amount_cents), 0),
        )
        .where(MachineRevenueClaim.machine_id.in_(machine_ids))
        .group_by(MachineRevenueClaim.machine_id)
    ).all()

    summary = {
        machine_id: {
            "projected_cents": 0,
            "claimed_cents": 0,
            "claimable_cents": 0,
            "projected_pwr_wei": 0,
            "claimed_pwr_wei": 0,
            "claimable_pwr_wei": 0,
        }
        for machine_id in machine_ids
    }
    for machine_id, projected_cents in projected_rows:
        summary[machine_id]["projected_cents"] = int(projected_cents or 0)
    for machine_id, claimed_cents in claimed_rows:
        summary[machine_id]["claimed_cents"] = int(claimed_cents or 0)

    projected_entries = db.scalars(select(RevenueEntry).where(RevenueEntry.machine_id.in_(machine_ids))).all()
    for entry in projected_entries:
        summary[entry.machine_id]["projected_pwr_wei"] += revenue_entry_machine_share_wei(entry=entry, db=db)

    claim_entries = db.scalars(select(MachineRevenueClaim).where(MachineRevenueClaim.machine_id.in_(machine_ids))).all()
    for claim in claim_entries:
        summary[claim.machine_id]["claimed_pwr_wei"] += machine_revenue_claim_amount_wei(claim)

    for machine_id, values in summary.items():
        values["claimable_cents"] = max(0, values["projected_cents"] - values["claimed_cents"])
        values["claimable_pwr_wei"] = max(0, values["projected_pwr_wei"] - values["claimed_pwr_wei"])
    return summary


def _locked_beneficiaries_by_machine(*, machine_ids: list[str], db: Session) -> dict[str, list[str]]:
    if not machine_ids:
        return {}
    rows = db.execute(
        select(RevenueEntry.machine_id, RevenueEntry.beneficiary_user_id)
        .where(RevenueEntry.machine_id.in_(machine_ids))
        .distinct()
    ).all()
    summary: dict[str, list[str]] = {machine_id: [] for machine_id in machine_ids}
    for machine_id, beneficiary_user_id in rows:
        beneficiaries = summary.setdefault(machine_id, [])
        if beneficiary_user_id not in beneficiaries:
            beneficiaries.append(beneficiary_user_id)
    return summary


def _active_listings_by_machine(*, machine_ids: list[str], db: Session) -> dict[str, MachineListing]:
    if not machine_ids:
        return {}

    now = datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(MachineListing)
            .where(
                MachineListing.machine_id.in_(machine_ids),
                MachineListing.state == "active",
            )
            .order_by(MachineListing.listed_at.desc())
        )
        .scalars()
        .all()
    )

    summary: dict[str, MachineListing] = {}
    for listing in rows:
        if listing.machine_id is None:
            continue
        expires_at = listing.expires_at
        if expires_at is not None:
            normalized_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
            if normalized_expires_at <= now:
                continue
        summary.setdefault(listing.machine_id, listing)
    return summary


def _runtime_snapshot_response(machine_id: str | None = None) -> MachineRuntimeSnapshotResponse:
    snapshot = get_shared_hardware_simulator(machine_id).snapshot()
    capacity_utilization = (
        snapshot.used_capacity_units / snapshot.total_capacity_units
        if snapshot.total_capacity_units > 0
        else 0.0
    )
    return MachineRuntimeSnapshotResponse(
        used_capacity_units=snapshot.used_capacity_units,
        total_capacity_units=snapshot.total_capacity_units,
        capacity_utilization=round(capacity_utilization, 4),
        used_memory_mb=snapshot.used_memory_mb,
        total_memory_mb=snapshot.total_memory_mb,
        memory_utilization=round(snapshot.memory_utilization, 4),
        running_count=snapshot.running_count,
        queued_count=snapshot.queued_count,
        queue_utilization=round(snapshot.queue_utilization, 4),
        max_concurrency=snapshot.max_concurrency,
        max_queue_depth=snapshot.max_queue_depth,
    )


def _indicative_apr(projected_cents: int) -> float:
    if MACHINE_ASSET_COST_CENTS <= 0:
        return 0.0
    apr = (projected_cents * 12 * 100) / MACHINE_ASSET_COST_CENTS
    return round(apr, 2)


def _availability_from_runtime(snapshot: MachineRuntimeSnapshotResponse) -> int:
    pressure = max(snapshot.capacity_utilization, snapshot.memory_utilization, snapshot.queue_utilization)
    return max(0, min(100, int(round((1 - pressure) * 100))))


def _to_listing_response(listing: MachineListing) -> MachineListingSummaryResponse:
    return MachineListingSummaryResponse(
        onchain_listing_id=listing.onchain_listing_id,
        seller_chain_address=listing.seller_chain_address,
        buyer_chain_address=listing.buyer_chain_address,
        payment_token_address=listing.payment_token_address,
        payment_token_symbol=listing.payment_token_symbol,
        payment_token_decimals=listing.payment_token_decimals,
        price_units=int(listing.price_units),
        state=listing.state,
        expires_at=listing.expires_at,
        listed_at=listing.listed_at,
        cancelled_at=listing.cancelled_at,
        filled_at=listing.filled_at,
    )


def _to_machine_response(
    machine: Machine,
    *,
    revenue_summary: dict[str, int] | None = None,
    locked_beneficiary_user_ids: list[str] | None = None,
    active_listing: MachineListing | None = None,
) -> MachineResponse:
    summary = revenue_summary or {
        "projected_cents": 0,
        "claimed_cents": 0,
        "claimable_cents": 0,
        "projected_pwr_wei": 0,
        "claimed_pwr_wei": 0,
        "claimable_pwr_wei": 0,
    }
    blocking_reasons = _transfer_blocking_reasons(machine)
    runtime_snapshot = _runtime_snapshot_response(machine.id)
    locked_cents = summary["claimable_cents"] if machine.has_unsettled_revenue else 0
    projected_pwr = pwr_wei_to_float(summary["projected_pwr_wei"])
    claimed_pwr = pwr_wei_to_float(summary["claimed_pwr_wei"])
    claimable_pwr = pwr_wei_to_float(summary["claimable_pwr_wei"])
    return MachineResponse(
        id=machine.id,
        onchain_machine_id=machine.onchain_machine_id,
        display_name=machine.display_name,
        owner_user_id=machine.owner_user_id,
        owner_chain_address=machine.owner_chain_address,
        ownership_source=machine.ownership_source,
        owner_projection_last_event_id=machine.owner_projection_last_event_id,
        owner_projected_at=machine.owner_projected_at,
        pending_transfer_new_owner_user_id=machine.pending_transfer_new_owner_user_id,
        has_active_tasks=machine.has_active_tasks,
        has_unsettled_revenue=machine.has_unsettled_revenue,
        transfer_ready=not blocking_reasons,
        transfer_blocking_reasons=blocking_reasons,
        projected_cents=summary["projected_cents"],
        claimed_cents=summary["claimed_cents"],
        claimable_cents=summary["claimable_cents"],
        projected_pwr=projected_pwr,
        claimed_pwr=claimed_pwr,
        claimable_pwr=claimable_pwr,
        locked_unsettled_revenue_cents=locked_cents,
        locked_unsettled_revenue_pwr=claimable_pwr if machine.has_unsettled_revenue else 0.0,
        locked_beneficiary_user_ids=(locked_beneficiary_user_ids or []) if locked_cents > 0 else [],
        profile_label=MOCK_MACHINE_SPEC["profile_label"],
        gpu_spec=MOCK_MACHINE_SPEC["gpu_spec"],
        supported_categories=list(MOCK_MACHINE_SPEC["supported_categories"]),
        hosted_by=MOCK_MACHINE_SPEC["hosted_by"],
        availability=_availability_from_runtime(runtime_snapshot),
        confirmed_revenue_30d_pwr=projected_pwr,
        indicative_apr=_indicative_apr(summary["projected_cents"]),
        active_listing=_to_listing_response(active_listing) if active_listing is not None else None,
        runtime_snapshot=runtime_snapshot,
        created_at=machine.created_at,
        updated_at=machine.updated_at,
    )


@router.post("", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineCreateRequest,
    db: Session = Depends(get_db),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> MachineResponse:
    onchain_machine_id = payload.onchain_machine_id
    ownership_source = "bootstrap"
    owner_chain_address = None
    if onchain_machine_id is None and onchain_lifecycle.enabled():
        token_uri = f"ipfs://outcomex-machine/{payload.owner_user_id}/{payload.display_name.replace(' ', '-').lower()}"
        minted = onchain_lifecycle.mint_machine_for_owner(
            owner_user_id=payload.owner_user_id,
            token_uri=token_uri,
        )
        if minted.onchain_machine_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Machine mint broadcasted but receipt did not expose machine id",
            )
        onchain_machine_id = minted.onchain_machine_id
        ownership_source = "chain"
        owner_chain_address = get_container().buyer_address_resolver.resolve_wallet(payload.owner_user_id)

    machine = Machine(
        display_name=payload.display_name,
        owner_user_id=payload.owner_user_id,
        onchain_machine_id=onchain_machine_id,
        ownership_source=ownership_source,
        owner_chain_address=owner_chain_address,
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return _to_machine_response(machine)


@router.get("", response_model=list[MachineResponse])
def list_machines(db: Session = Depends(get_db)) -> list[MachineResponse]:
    machines = list(db.scalars(select(Machine).order_by(Machine.created_at.desc())))
    revenue_summary = _machine_revenue_summary(machine_ids=[machine.id for machine in machines], db=db)
    locked_beneficiaries = _locked_beneficiaries_by_machine(machine_ids=[machine.id for machine in machines], db=db)
    active_listings = _active_listings_by_machine(machine_ids=[machine.id for machine in machines], db=db)
    return [
        _to_machine_response(
            machine,
            revenue_summary=revenue_summary.get(machine.id),
            locked_beneficiary_user_ids=locked_beneficiaries.get(machine.id),
            active_listing=active_listings.get(machine.id),
        )
        for machine in machines
    ]
