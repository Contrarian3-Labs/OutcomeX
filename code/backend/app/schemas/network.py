from pydantic import BaseModel, Field


class NetworkOverviewResponse(BaseModel):
    hosted_machines: int = Field(ge=0)
    live_capability_families: int = Field(ge=0)
    live_capability_family_labels: list[str] = Field(default_factory=list)
    confirmed_deliveries_30d: int = Field(ge=0)
    indicative_realized_apr_network: float = Field(ge=0)
    pwr_anchor_price_cents: int = Field(ge=1)


class OnchainIndexerRuntimeResponse(BaseModel):
    enabled: bool
    reason: str
    chain_id: int
    rpc_url: str
    poll_seconds: float = Field(ge=0)
    confirmation_depth: int = Field(ge=0)
    bootstrap_block: int = Field(ge=0)
    max_block_span: int = Field(ge=1)
    subscription_contracts: list[str] = Field(default_factory=list)
    last_indexed_block: int | None = None
