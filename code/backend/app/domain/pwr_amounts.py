from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.domain.models import Payment
from app.runtime.cost_service import PWR_QUANTIZE, get_runtime_cost_service

PWR_WEI = 10**18
BPS_DENOMINATOR = 10_000
PLATFORM_FEE_BPS = 1_000
VALID_PREVIEW_REJECT_REFUND_BPS = 7_000


def runtime_pwr_anchor_price_cents() -> int:
    return get_runtime_cost_service().pwr_anchor_price_cents


def parse_pwr_wei(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def cents_to_pwr_wei(amount_cents: int, *, anchor_price_cents: int | None = None) -> int:
    anchor = anchor_price_cents or runtime_pwr_anchor_price_cents()
    if amount_cents <= 0 or anchor <= 0:
        return 0
    return (amount_cents * PWR_WEI) // anchor


def pwr_wei_to_cents(amount_wei: int, *, anchor_price_cents: int | None = None) -> int:
    anchor = anchor_price_cents or runtime_pwr_anchor_price_cents()
    if amount_wei <= 0 or anchor <= 0:
        return 0
    cents = (
        Decimal(amount_wei) * Decimal(anchor) / Decimal(PWR_WEI)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def pwr_wei_to_float(amount_wei: str | int | None) -> float:
    parsed = parse_pwr_wei(amount_wei)
    if parsed <= 0:
        return 0.0
    amount = (Decimal(parsed) / Decimal(PWR_WEI)).quantize(PWR_QUANTIZE, rounding=ROUND_HALF_UP)
    return float(amount)


def pwr_payment_terms(payment: Payment | None) -> tuple[int | None, int | None]:
    if payment is None or payment.currency.upper() != "PWR":
        return None, None
    payload = dict(payment.provider_payload or {})
    direct_payload = dict(payload.get("direct_intent_payload") or {})
    raw_amount = direct_payload.get("pwr_amount")
    raw_anchor = direct_payload.get("pwr_anchor_price_cents")
    try:
        amount_wei = int(str(raw_amount)) if raw_amount is not None else None
        anchor_price_cents = int(raw_anchor) if raw_anchor not in {None, 0, "0"} else None
    except (TypeError, ValueError):
        return None, None
    return amount_wei, anchor_price_cents


def confirmed_pwr_split(amount_wei: int) -> tuple[int, int]:
    platform = (amount_wei * PLATFORM_FEE_BPS) // BPS_DENOMINATOR
    machine = amount_wei - platform
    return platform, machine


def rejected_valid_preview_pwr_split(amount_wei: int) -> tuple[int, int, int]:
    refund = (amount_wei * VALID_PREVIEW_REJECT_REFUND_BPS) // BPS_DENOMINATOR
    rejection_fee = amount_wei - refund
    platform = (rejection_fee * PLATFORM_FEE_BPS) // BPS_DENOMINATOR
    machine = rejection_fee - platform
    return refund, platform, machine
