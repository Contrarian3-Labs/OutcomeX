from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.domain.models import Machine, utc_now


@dataclass(frozen=True)
class MachineOwnershipProjectionResult:
    applied: bool
    machine_id: str
    owner_user_id: str | None
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
        machine = db.get(Machine, machine_id)
        if machine is None:
            return MachineOwnershipProjectionResult(
                applied=False,
                machine_id=machine_id,
                owner_user_id=None,
                reason="machine_not_found",
            )

        resolved_owner_user_id = self._owner_resolver(chain_owner)
        if resolved_owner_user_id is None:
            return MachineOwnershipProjectionResult(
                applied=False,
                machine_id=machine_id,
                owner_user_id=None,
                reason="owner_unresolved",
            )

        machine.owner_user_id = resolved_owner_user_id
        machine.ownership_source = "chain"
        machine.owner_projection_last_event_id = event_id
        machine.owner_projected_at = utc_now()
        machine.pending_transfer_new_owner_user_id = None
        machine.pending_transfer_keep_previous_setup = None
        machine.pending_transfer_requested_at = None
        db.add(machine)
        db.commit()

        return MachineOwnershipProjectionResult(
            applied=True,
            machine_id=machine.id,
            owner_user_id=machine.owner_user_id,
        )
