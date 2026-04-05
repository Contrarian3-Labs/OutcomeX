from pydantic import BaseModel, Field


class QuoteResponse(BaseModel):
    runtime_cost_cents: int = Field(ge=0)
    official_quote_cents: int = Field(gt=0)
    platform_fee_cents: int = Field(ge=0)
    machine_share_cents: int = Field(ge=0)
    pwr_quote: str
    currency: str = Field(default="USD", min_length=3, max_length=8)
    pricing_version: str = "phase1_v2"
