from app.domain.enums import OrderState
from app.domain.rules import (
    calculate_confirmed_settlement_breakdown,
    calculate_failed_or_no_valid_preview_breakdown,
    calculate_rejected_valid_preview_breakdown,
    calculate_revenue_split,
    can_start_settlement,
    can_transfer_machine,
    has_sufficient_payment,
    is_dividend_eligible,
)


def test_revenue_split_is_10_90() -> None:
    platform_fee, machine_share = calculate_revenue_split(1000)
    assert platform_fee == 100
    assert machine_share == 900


def test_revenue_split_uses_deterministic_integer_math() -> None:
    platform_fee, machine_share = calculate_revenue_split(101)
    assert platform_fee == 10
    assert machine_share == 91


def test_settlement_needs_confirmation() -> None:
    assert can_start_settlement(OrderState.EXECUTING, None) is False
    assert can_start_settlement(OrderState.RESULT_CONFIRMED, None) is False


def test_self_use_is_not_dividend_eligible() -> None:
    assert is_dividend_eligible("user-a", "user-a") is False
    assert is_dividend_eligible("user-a", "user-b") is True


def test_transfer_blocked_when_tasks_or_unsettled_revenue() -> None:
    assert can_transfer_machine(has_active_tasks=True, has_unsettled_revenue=False) is False
    assert can_transfer_machine(has_active_tasks=False, has_unsettled_revenue=True) is False
    assert can_transfer_machine(has_active_tasks=False, has_unsettled_revenue=False) is True


def test_payment_must_cover_order_amount() -> None:
    assert has_sufficient_payment(required_amount_cents=1000, paid_amount_cents=1000) is True
    assert has_sufficient_payment(required_amount_cents=1000, paid_amount_cents=1200) is True
    assert has_sufficient_payment(required_amount_cents=1000, paid_amount_cents=999) is False


def test_confirmed_settlement_breakdown_matches_10_90_split() -> None:
    breakdown = calculate_confirmed_settlement_breakdown(1000)
    assert breakdown.refund_to_buyer_cents == 0
    assert breakdown.platform_fee_cents == 100
    assert breakdown.machine_share_cents == 900


def test_rejected_valid_preview_breakdown_is_70_3_27() -> None:
    breakdown = calculate_rejected_valid_preview_breakdown(1000)
    assert breakdown.refund_to_buyer_cents == 700
    assert breakdown.rejection_fee_cents == 300
    assert breakdown.platform_fee_cents == 30
    assert breakdown.machine_share_cents == 270


def test_failed_or_no_valid_preview_breakdown_is_full_refund() -> None:
    breakdown = calculate_failed_or_no_valid_preview_breakdown(1000)
    assert breakdown.refund_to_buyer_cents == 1000
    assert breakdown.platform_fee_cents == 0
    assert breakdown.machine_share_cents == 0
