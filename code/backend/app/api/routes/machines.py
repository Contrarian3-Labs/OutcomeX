from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.container import get_container
from app.domain.models import Machine, MachineRevenueClaim, RevenueEntry
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.runtime.cost_service import get_runtime_cost_service
from app.runtime.hardware_simulator import get_shared_hardware_simulator
from app.schemas.machine import (
    MachineCreateRequest,
    MachineResponse,
    MachineRuntimeSnapshotResponse,
)

router = APIRouter()

PWR_QUANTIZE = Decimal("0.0001")
MACHINE_ASSET_COST_CENTS = 399_900
MOCK_MACHINE_SPEC = {
    "profile_label": "OutcomeX Hosted Mac Studio",
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

    summary = {machine_id: {"projected_cents": 0, "claimed_cents": 0, "claimable_cents": 0} for machine_id in machine_ids}
    for machine_id, projected_cents in projected_rows:
        summary[machine_id]["projected_cents"] = int(projected_cents or 0)
    for machine_id, claimed_cents in claimed_rows:
        summary[machine_id]["claimed_cents"] = int(claimed_cents or 0)
    for machine_id, values in summary.items():
        values["claimable_cents"] = max(0, values["projected_cents"] - values["claimed_cents"])
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


def _cents_to_pwr(amount_cents: int) -> float:
    anchor_price_cents = get_runtime_cost_service().pwr_anchor_price_cents
    if anchor_price_cents <= 0:
        return 0.0
    pwr_amount = (Decimal(amount_cents) / Decimal(anchor_price_cents)).quantize(PWR_QUANTIZE, rounding=ROUND_HALF_UP)
    return float(pwr_amount)


def _indicative_apr(projected_cents: int) -> float:
    if MACHINE_ASSET_COST_CENTS <= 0:
        return 0.0
    apr = (projected_cents * 12 * 100) / MACHINE_ASSET_COST_CENTS
    return round(apr, 2)


def _availability_from_runtime(snapshot: MachineRuntimeSnapshotResponse) -> int:
    pressure = max(snapshot.capacity_utilization, snapshot.memory_utilization, snapshot.queue_utilization)
    return max(0, min(100, int(round((1 - pressure) * 100))))


def _to_machine_response(
    machine: Machine,
    *,
    revenue_summary: dict[str, int] | None = None,
    locked_beneficiary_user_ids: list[str] | None = None,
) -> MachineResponse:
    summary = revenue_summary or {"projected_cents": 0, "claimed_cents": 0, "claimable_cents": 0}
    blocking_reasons = _transfer_blocking_reasons(machine)
    runtime_snapshot = _runtime_snapshot_response(machine.id)
    locked_cents = summary["claimable_cents"] if machine.has_unsettled_revenue else 0
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
        locked_unsettled_revenue_cents=locked_cents,
        locked_unsettled_revenue_pwr=_cents_to_pwr(locked_cents),
        locked_beneficiary_user_ids=(locked_beneficiary_user_ids or []) if locked_cents > 0 else [],
        profile_label=MOCK_MACHINE_SPEC["profile_label"],
        gpu_spec=MOCK_MACHINE_SPEC["gpu_spec"],
        supported_categories=list(MOCK_MACHINE_SPEC["supported_categories"]),
        hosted_by=MOCK_MACHINE_SPEC["hosted_by"],
        availability=_availability_from_runtime(runtime_snapshot),
        confirmed_revenue_30d_pwr=_cents_to_pwr(summary["projected_cents"]),
        claimable_pwr=_cents_to_pwr(summary["claimable_cents"]),
        indicative_apr=_indicative_apr(summary["projected_cents"]),
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
    return [
        _to_machine_response(
            machine,
            revenue_summary=revenue_summary.get(machine.id),
            locked_beneficiary_user_ids=locked_beneficiaries.get(machine.id),
        )
        for machine in machines
    ]
