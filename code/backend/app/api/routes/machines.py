from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.models import Machine, utc_now
from app.domain.rules import can_transfer_machine
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.schemas.machine import (
    MachineCreateRequest,
    MachineResponse,
    MachineTransferRequest,
    MachineTransferResponse,
)

router = APIRouter()


@router.post("", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineCreateRequest,
    db: Session = Depends(get_db),
    onchain_lifecycle: OnchainLifecycleService = Depends(get_onchain_lifecycle_service),
) -> Machine:
    onchain_machine_id = payload.onchain_machine_id
    ownership_source = "bootstrap"
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

    machine = Machine(
        display_name=payload.display_name,
        owner_user_id=payload.owner_user_id,
        onchain_machine_id=onchain_machine_id,
        ownership_source=ownership_source,
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine


@router.get("", response_model=list[MachineResponse])
def list_machines(db: Session = Depends(get_db)) -> list[Machine]:
    return list(db.scalars(select(Machine).order_by(Machine.created_at.desc())))


@router.post("/{machine_id}/transfer", response_model=MachineTransferResponse)
def transfer_machine(
    machine_id: str,
    payload: MachineTransferRequest,
    db: Session = Depends(get_db),
) -> MachineTransferResponse:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    if not can_transfer_machine(machine.has_active_tasks, machine.has_unsettled_revenue):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Machine transfer blocked due to active tasks or unsettled revenue",
        )

    previous_owner = machine.owner_user_id
    machine.pending_transfer_new_owner_user_id = payload.new_owner_user_id
    machine.pending_transfer_keep_previous_setup = payload.keep_previous_setup
    machine.pending_transfer_requested_at = utc_now()
    db.add(machine)
    db.commit()

    return MachineTransferResponse(
        machine_id=machine.id,
        previous_owner_user_id=previous_owner,
        canonical_owner_user_id=machine.owner_user_id,
        new_owner_user_id=payload.new_owner_user_id,
        setup_carried_over=payload.keep_previous_setup,
        transfer_status="intent_recorded",
        owner_updated=False,
    )
