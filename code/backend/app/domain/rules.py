from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import OrderState

PLATFORM_FEE_NUMERATOR = 10
PLATFORM_FEE_DENOMINATOR = 100
VALID_PREVIEW_REJECT_REFUND_NUMERATOR = 70
VALID_PREVIEW_REJECT_REFUND_DENOMINATOR = 100


@dataclass(frozen=True)
class SettlementBreakdownCents:
    gross_amount_cents: int
    refund_to_buyer_cents: int
    platform_fee_cents: int
    machine_share_cents: int
    rejection_fee_cents: int = 0


def calculate_revenue_split(total_amount_cents: int) -> tuple[int, int]:
    if total_amount_cents < 0:
        raise ValueError("total_amount_cents must be non-negative")
    platform_fee = (total_amount_cents * PLATFORM_FEE_NUMERATOR) // PLATFORM_FEE_DENOMINATOR
    machine_side = total_amount_cents - platform_fee
    return platform_fee, machine_side


def calculate_confirmed_settlement_breakdown(total_amount_cents: int) -> SettlementBreakdownCents:
    platform_fee, machine_share = calculate_revenue_split(total_amount_cents)
    return SettlementBreakdownCents(
        gross_amount_cents=total_amount_cents,
        refund_to_buyer_cents=0,
        platform_fee_cents=platform_fee,
        machine_share_cents=machine_share,
    )


def calculate_rejected_valid_preview_breakdown(total_amount_cents: int) -> SettlementBreakdownCents:
    if total_amount_cents < 0:
        raise ValueError("total_amount_cents must be non-negative")
    refund_to_buyer = (total_amount_cents * VALID_PREVIEW_REJECT_REFUND_NUMERATOR) // VALID_PREVIEW_REJECT_REFUND_DENOMINATOR
    rejection_fee = total_amount_cents - refund_to_buyer
    platform_fee, machine_share = calculate_revenue_split(rejection_fee)
    return SettlementBreakdownCents(
        gross_amount_cents=total_amount_cents,
        refund_to_buyer_cents=refund_to_buyer,
        platform_fee_cents=platform_fee,
        machine_share_cents=machine_share,
        rejection_fee_cents=rejection_fee,
    )


def calculate_failed_or_no_valid_preview_breakdown(total_amount_cents: int) -> SettlementBreakdownCents:
    if total_amount_cents < 0:
        raise ValueError("total_amount_cents must be non-negative")
    return SettlementBreakdownCents(
        gross_amount_cents=total_amount_cents,
        refund_to_buyer_cents=total_amount_cents,
        platform_fee_cents=0,
        machine_share_cents=0,
    )


def has_sufficient_payment(required_amount_cents: int, paid_amount_cents: int) -> bool:
    if required_amount_cents < 0:
        raise ValueError("required_amount_cents must be non-negative")
    if paid_amount_cents < 0:
        raise ValueError("paid_amount_cents must be non-negative")
    return paid_amount_cents >= required_amount_cents


def can_start_settlement(order_state: OrderState, result_confirmed_at: datetime | None) -> bool:
    return order_state == OrderState.RESULT_CONFIRMED and result_confirmed_at is not None


def is_dividend_eligible(order_user_id: str, machine_owner_user_id: str) -> bool:
    # Owner self-use is explicitly excluded from dividends.
    return order_user_id != machine_owner_user_id


def can_transfer_machine(has_active_tasks: bool, has_unsettled_revenue: bool) -> bool:
    return not has_active_tasks and not has_unsettled_revenue
