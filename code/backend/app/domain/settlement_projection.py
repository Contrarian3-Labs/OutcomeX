from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.models import Machine, Order, RevenueEntry, SettlementRecord
from app.domain.rules import calculate_revenue_split


def ensure_confirmed_settlement_projection(
    *,
    db: Session,
    order: Order,
    machine: Machine,
    gross_amount_cents: int,
    distributed_at: datetime | None = None,
) -> tuple[SettlementRecord, RevenueEntry]:
    if order.settlement_beneficiary_user_id is None:
        raise ValueError("settlement_beneficiary_missing")
    if order.settlement_is_self_use is None:
        raise ValueError("settlement_self_use_missing")
    if order.settlement_is_dividend_eligible is None:
        raise ValueError("settlement_dividend_flag_missing")

    distributed_at = distributed_at or datetime.now(timezone.utc)
    platform_fee_cents, machine_share_cents = calculate_revenue_split(gross_amount_cents)

    settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == order.id).first()
    if settlement is None:
        settlement = SettlementRecord(
            order_id=order.id,
            gross_amount_cents=gross_amount_cents,
            platform_fee_cents=platform_fee_cents,
            machine_share_cents=machine_share_cents,
            state=order.settlement_state,
            distributed_at=distributed_at,
        )
    settlement.gross_amount_cents = gross_amount_cents
    settlement.platform_fee_cents = platform_fee_cents
    settlement.machine_share_cents = machine_share_cents
    settlement.state = order.settlement_state
    settlement.distributed_at = distributed_at
    db.add(settlement)
    db.flush()

    entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == order.id).first()
    if entry is None:
        entry = RevenueEntry(
            order_id=order.id,
            settlement_id=settlement.id,
            machine_id=machine.id,
            beneficiary_user_id=order.settlement_beneficiary_user_id,
            gross_amount_cents=gross_amount_cents,
            platform_fee_cents=platform_fee_cents,
            machine_share_cents=machine_share_cents,
            is_self_use=order.settlement_is_self_use,
            is_dividend_eligible=order.settlement_is_dividend_eligible,
        )
    else:
        entry.settlement_id = settlement.id
        entry.machine_id = machine.id
        entry.beneficiary_user_id = order.settlement_beneficiary_user_id
        entry.gross_amount_cents = gross_amount_cents
        entry.platform_fee_cents = platform_fee_cents
        entry.machine_share_cents = machine_share_cents
        entry.is_self_use = order.settlement_is_self_use
        entry.is_dividend_eligible = order.settlement_is_dividend_eligible
    db.add(entry)

    machine.has_active_tasks = False
    machine.has_unsettled_revenue = bool(order.settlement_is_dividend_eligible and machine_share_cents > 0)
    db.add(machine)
    return settlement, entry
