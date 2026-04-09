import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.onchain.lifecycle_service import reset_onchain_lifecycle_service_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Machine, MachineListing
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    previous_onchain_rpc_url = os.environ.get("OUTCOMEX_ONCHAIN_RPC_URL")
    db_path = tmp_path / "marketplace-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()

    with TestClient(create_app()) as test_client:
        yield test_client

    if previous_onchain_rpc_url is None:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
    else:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = previous_onchain_rpc_url
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()


def test_marketplace_listings_returns_only_active_non_expired_projection_rows(client: TestClient) -> None:
    container = get_container()
    now = datetime.now(timezone.utc)

    with container.session_factory() as db:
        machine = Machine(
            id="machine-1",
            onchain_machine_id="7",
            display_name="OutcomeX Hosted Machine",
            owner_user_id="owner-1",
            owner_chain_address="0x1111111111111111111111111111111111111111",
            ownership_source="chain",
        )
        db.add(machine)
        db.flush()

        db.add(
            MachineListing(
                onchain_listing_id="11",
                machine_id=machine.id,
                onchain_machine_id="7",
                seller_chain_address="0x1111111111111111111111111111111111111111",
                payment_token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                payment_token_symbol="USDC",
                payment_token_decimals=6,
                price_units=1_250_000,
                state="active",
                expires_at=now + timedelta(days=7),
                listed_at=now,
            )
        )
        db.add(
            MachineListing(
                onchain_listing_id="12",
                machine_id=machine.id,
                onchain_machine_id="7",
                seller_chain_address="0x1111111111111111111111111111111111111111",
                payment_token_address="0x372325443233fEbaC1F6998aC750276468c83CC6".lower(),
                payment_token_symbol="USDT",
                payment_token_decimals=6,
                price_units=2_000_000,
                state="cancelled",
                expires_at=now + timedelta(days=7),
                listed_at=now - timedelta(hours=1),
                cancelled_at=now - timedelta(minutes=5),
            )
        )
        db.add(
            MachineListing(
                onchain_listing_id="13",
                machine_id=machine.id,
                onchain_machine_id="7",
                seller_chain_address="0x1111111111111111111111111111111111111111",
                payment_token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                payment_token_symbol="USDC",
                payment_token_decimals=6,
                price_units=3_000_000,
                state="active",
                expires_at=now - timedelta(minutes=1),
                listed_at=now - timedelta(hours=2),
            )
        )
        db.commit()

    response = client.get("/api/v1/marketplace/listings")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1

    listing = payload[0]
    assert listing["onchain_listing_id"] == "11"
    assert listing["payment_token_symbol"] == "USDC"
    assert listing["price_units"] == 1_250_000
    assert listing["machine"]["id"] == "machine-1"
    assert listing["machine"]["onchain_machine_id"] == "7"
    assert listing["machine"]["display_name"] == "OutcomeX Hosted Machine"
    assert listing["machine"]["active_listing"]["onchain_listing_id"] == "11"
