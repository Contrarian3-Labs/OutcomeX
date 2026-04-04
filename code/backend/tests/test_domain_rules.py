from app.domain.enums import OrderState
from app.domain.rules import calculate_revenue_split, can_start_settlement, can_transfer_machine, is_dividend_eligible


def test_revenue_split_is_10_90() -> None:
    platform_fee, machine_share = calculate_revenue_split(1000)
    assert platform_fee == 100
    assert machine_share == 900


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

