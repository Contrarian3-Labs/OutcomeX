from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.models import Machine, utc_now
from app.indexer.recovery import fallback_machine_display_name, projection_uuid, resolve_projected_user_id


@dataclass(frozen=True)
class MachineOwnershipProjectionResult:
    applied: bool
    machine_id: str
    owner_user_id: str | None
    chain_owner: str | None = None
    reason: str | None = None


class MachineOwnershipProjectionIntegrator:
    """Apply indexed chain ownership as canonical backend machine ownership."""

    def __init__(
        self,
        *,
        owner_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._owner_resolver = owner_resolver or (lambda chain_owner: chain_owner)

    def apply_machine_owner_projection(
        self,
        *,
        db: Session,
        machine_id: str,
        chain_owner: str,
        event_id: str,
    ) -> MachineOwnershipProjectionResult:
        machine = db.query(Machine).filter(
            or_(Machine.id == machine_id, Machine.onchain_machine_id == machine_id)
        ).first()
        if machine is None:
            machine = Machine(
                id=projection_uuid("machine", machine_id),
                onchain_machine_id=machine_id,
                display_name=fallback_machine_display_name(machine_id),
                owner_user_id=resolve_projected_user_id(
                    self._owner_resolver,
                    chain_owner,
                    fallback_prefix="machine-owner",
                    natural_key=machine_id,
                ),
                owner_chain_address=chain_owner,
                ownership_source="chain",
            )

        machine.owner_chain_address = chain_owner
        machine.owner_projection_last_event_id = event_id
        machine.owner_projected_at = utc_now()

        resolved_owner_user_id = resolve_projected_user_id(
            self._owner_resolver,
            chain_owner,
            fallback_prefix="machine-owner",
            natural_key=machine_id,
        )

        machine.owner_user_id = resolved_owner_user_id
        machine.ownership_source = "chain"
        machine.pending_transfer_new_owner_user_id = None
        machine.pending_transfer_keep_previous_setup = None
        machine.pending_transfer_requested_at = None
        db.add(machine)
        db.commit()

        return MachineOwnershipProjectionResult(
            applied=True,
            machine_id=machine_id,
            owner_user_id=machine.owner_user_id,
            chain_owner=chain_owner,
        )
