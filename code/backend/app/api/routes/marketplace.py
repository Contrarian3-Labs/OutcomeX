from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dependency_container
from app.core.container import Container
from app.api.routes.machines import (
    _active_listings_by_machine,
    _locked_beneficiaries_by_machine,
    _machine_revenue_summary,
    _to_machine_response,
)
from app.domain.models import Machine, MachineListing
from app.onchain.manual_projection_sync import sync_projection_from_tx_hash
from app.schemas.marketplace import MarketplaceListingResponse, MarketplaceSyncRequest, MarketplaceSyncResponse

router = APIRouter()


@router.get("/listings", response_model=list[MarketplaceListingResponse])
def list_marketplace_listings(db: Session = Depends(get_db)) -> list[MarketplaceListingResponse]:
    listings = list(
        db.scalars(
            select(MachineListing)
            .where(MachineListing.state == "active")
            .order_by(MachineListing.listed_at.desc())
        )
    )

    now = datetime.now(timezone.utc)
    active_listings: list[MachineListing] = []
    machine_ids: list[str] = []
    machines_by_id: dict[str, Machine] = {}

    for listing in listings:
        expires_at = listing.expires_at
        if expires_at is not None:
            normalized_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
            if normalized_expires_at <= now:
                continue
        if listing.machine_id is None:
            continue
        machine = db.get(Machine, listing.machine_id)
        if machine is None:
            continue
        active_listings.append(listing)
        machines_by_id[machine.id] = machine
        machine_ids.append(machine.id)

    revenue_summary = _machine_revenue_summary(machine_ids=machine_ids, db=db)
    locked_beneficiaries = _locked_beneficiaries_by_machine(machine_ids=machine_ids, db=db)
    listing_lookup = _active_listings_by_machine(machine_ids=machine_ids, db=db)

    response: list[MarketplaceListingResponse] = []
    for listing in active_listings:
        machine = machines_by_id[listing.machine_id]
        response.append(
            MarketplaceListingResponse(
                onchain_listing_id=listing.onchain_listing_id,
                machine_id=listing.machine_id,
                onchain_machine_id=listing.onchain_machine_id,
                seller_chain_address=listing.seller_chain_address,
                buyer_chain_address=listing.buyer_chain_address,
                payment_token_address=listing.payment_token_address,
                payment_token_symbol=listing.payment_token_symbol,
                payment_token_decimals=listing.payment_token_decimals,
                price_units=int(listing.price_units),
                state=listing.state,
                expires_at=listing.expires_at,
                listed_at=listing.listed_at,
                cancelled_at=listing.cancelled_at,
                filled_at=listing.filled_at,
                machine=_to_machine_response(
                    machine,
                    revenue_summary=revenue_summary.get(machine.id),
                    locked_beneficiary_user_ids=locked_beneficiaries.get(machine.id),
                    active_listing=listing_lookup.get(machine.id),
                ),
            )
        )
    return response


@router.post("/sync-onchain", response_model=MarketplaceSyncResponse)
def sync_marketplace_projection_from_tx_hash(
    payload: MarketplaceSyncRequest,
    container: Container = Depends(get_dependency_container),
) -> MarketplaceSyncResponse:
    result = sync_projection_from_tx_hash(
        tx_hash=payload.tx_hash,
        session_factory=container.session_factory,
        owner_resolver=container.buyer_address_resolver.resolve_user_id,
        settings=container.settings,
    )
    return MarketplaceSyncResponse(
        tx_hash=result.tx_hash,
        receipt_found=result.receipt_found,
        applied_events=result.applied_events,
        event_names=list(result.event_names),
        listing_ids=list(result.listing_ids),
        machine_ids=list(result.machine_ids),
    )
