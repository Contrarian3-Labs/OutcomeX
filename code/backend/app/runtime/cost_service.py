from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from math import ceil

from app.domain.rules import calculate_revenue_split
from app.schemas.quote import QuoteResponse

PWR_QUANTIZE = Decimal("0.0001")


class RuntimeCostService:
    pricing_version = "phase1_v3"

    def __init__(self, *, minimum_margin_cents: int = 60, pwr_anchor_price_cents: int = 25) -> None:
        self.minimum_margin_cents = minimum_margin_cents
        self.pwr_anchor_price_cents = pwr_anchor_price_cents

    def quote_for_prompt(self, prompt: str) -> QuoteResponse:
        prompt_units = max(1, ceil(len(prompt.strip() or "plan") / 24))
        official_quote_cents = 420 + (prompt_units * 55)
        return self.quote_for_order_amount(official_quote_cents)

    def quote_for_order_amount(self, official_quote_cents: int) -> QuoteResponse:
        if official_quote_cents <= 0:
            raise ValueError("official_quote_cents must be positive")

        target_margin_cents = max(self.minimum_margin_cents, official_quote_cents // 4)
        runtime_cost_cents = max(0, official_quote_cents - target_margin_cents)
        platform_fee_cents, machine_share_cents = calculate_revenue_split(official_quote_cents)
        pwr_quote = (
            Decimal(machine_share_cents) / Decimal(self.pwr_anchor_price_cents)
        ).quantize(PWR_QUANTIZE, rounding=ROUND_HALF_UP)
        return QuoteResponse(
            runtime_cost_cents=runtime_cost_cents,
            official_quote_cents=official_quote_cents,
            platform_fee_cents=platform_fee_cents,
            machine_share_cents=machine_share_cents,
            pwr_quote=f"{pwr_quote:.4f}",
            pwr_anchor_price_cents=self.pwr_anchor_price_cents,
            currency="USD",
            pricing_version=self.pricing_version,
        )


@lru_cache
def get_runtime_cost_service() -> RuntimeCostService:
    return RuntimeCostService()
