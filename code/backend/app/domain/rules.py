from datetime import datetime

from app.domain.enums import OrderState

PLATFORM_FEE_RATE = 0.10
MACHINE_SHARE_RATE = 0.90


def calculate_revenue_split(total_amount_cents: int) -> tuple[int, int]:
    if total_amount_cents < 0:
        raise ValueError("total_amount_cents must be non-negative")
    platform_fee = int(round(total_amount_cents * PLATFORM_FEE_RATE))
    machine_side = total_amount_cents - platform_fee
    return platform_fee, machine_side


def can_start_settlement(order_state: OrderState, result_confirmed_at: datetime | None) -> bool:
    return order_state == OrderState.RESULT_CONFIRMED and result_confirmed_at is not None


def is_dividend_eligible(order_user_id: str, machine_owner_user_id: str) -> bool:
    # Owner self-use is explicitly excluded from dividends.
    return order_user_id != machine_owner_user_id


def can_transfer_machine(has_active_tasks: bool, has_unsettled_revenue: bool) -> bool:
    return not has_active_tasks and not has_unsettled_revenue

