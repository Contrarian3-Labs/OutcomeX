from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domain.models import Machine
from app.onchain.lifecycle_service import OnchainLifecycleService, get_onchain_lifecycle_service
from app.schemas.machine import (
    MachineCreateRequest,
    MachineResponse,
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


