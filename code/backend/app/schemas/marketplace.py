from datetime import datetime

from pydantic import BaseModel

from app.schemas.machine import MachineResponse


class MarketplaceListingResponse(BaseModel):
    onchain_listing_id: str
    machine_id: str | None
    onchain_machine_id: str | None
    seller_chain_address: str
    buyer_chain_address: str | None
    payment_token_address: str
    payment_token_symbol: str | None
    payment_token_decimals: int | None
    price_units: int
    state: str
    expires_at: datetime | None
    listed_at: datetime
    cancelled_at: datetime | None
    filled_at: datetime | None
    machine: MachineResponse
