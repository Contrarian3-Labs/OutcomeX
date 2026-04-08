import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Machine, SettlementClaimRecord
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "revenue-claims.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ONCHAIN_USDC_ADDRESS"] = "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"
    os.environ["OUTCOMEX_ONCHAIN_USDT_ADDRESS"] = "0x372325443233fEbaC1F6998aC750276468c83CC6"
    os.environ["OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS"] = "0x0000000000000000000000000000000000000A11"
    reset_settings_cache()
    reset_container_cache()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def test_list_revenue_claims_returns_descending_unified_claim_history(client: TestClient) -> None:
    container = get_container()
    with container.session_factory() as db:
        db.add(Machine(id="machine-1", display_name="node-1", owner_user_id="owner-1"))
        db.add_all(
            [
                SettlementClaimRecord(
                    event_id="evt-1",
                    claim_kind="machine_revenue",
                    claimant_user_id="owner-1",
                    account_address="0xowner",
                    token_address="0x0000000000000000000000000000000000000a11",
                    amount_cents=900,
                    tx_hash="0xclaim-machine",
                    machine_id="machine-1",
                ),
                SettlementClaimRecord(
                    event_id="evt-2",
                    claim_kind="refund",
                    claimant_user_id="owner-1",
                    account_address="0xowner",
                    token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                    amount_cents=700,
                    tx_hash="0xclaim-refund",
                    machine_id=None,
                ),
                SettlementClaimRecord(
                    event_id="evt-3",
                    claim_kind="platform_revenue",
                    claimant_user_id="platform",
                    account_address="0xtreasury",
                    token_address="0x372325443233febac1f6998ac750276468c83cc6",
                    amount_cents=30,
                    tx_hash="0xclaim-platform",
                    machine_id=None,
                ),
            ]
        )
        db.commit()

    response = client.get("/api/v1/revenue/accounts/owner-1/claims")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["claim_kind"] == "refund"
    assert payload[0]["currency"] == "USDC"
    assert payload[1]["claim_kind"] == "machine_revenue"
    assert payload[1]["currency"] == "PWR"
