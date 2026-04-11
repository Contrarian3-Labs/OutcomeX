import json
import os

import pytest

from app.core.config import get_settings, reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Machine, MachineListing, PrimaryIssuancePurchase, PrimaryIssuanceSku
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.onchain.lifecycle_service import MintedMachineReceipt
from scripts.prepare_local_browser_demo import (
    PRIMARY_ISSUANCE_INITIAL_STOCK,
    _build_demo_wallet_resolver,
    format_seed_report,
    seed_demo_projection_state,
)
import scripts.prepare_local_browser_demo as prepare_local_browser_demo


class FakeLifecycleService:
    def __init__(self) -> None:
        self.mint_calls: list[dict[str, str]] = []
        self._counter = 0

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str) -> MintedMachineReceipt:
        self._counter += 1
        onchain_machine_id = str(7000 + self._counter)
        self.mint_calls.append(
            {
                "owner_user_id": owner_user_id,
                "token_uri": token_uri,
                "onchain_machine_id": onchain_machine_id,
            }
        )
        return MintedMachineReceipt(
            tx_hash=f"0xmint{self._counter}",
            receipt=None,
            onchain_machine_id=onchain_machine_id,
        )


@pytest.fixture
def seed_context(tmp_path):
    db_path = tmp_path / "prepare-local-browser-demo.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = json.dumps(
        {
            "buyer-1": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            "owner-1": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            "owner-2": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
            "owner-3": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        }
    )
    os.environ["OUTCOMEX_ONCHAIN_USDC_ADDRESS"] = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    reset_settings_cache()
    reset_container_cache()
    settings = get_settings()
    container = get_container()
    resolver = BuyerAddressResolver.from_json(settings.buyer_wallet_map_json)
    lifecycle = FakeLifecycleService()

    yield settings, container, resolver, lifecycle

    reset_settings_cache()
    reset_container_cache()


def test_seed_demo_projection_state_creates_three_machines_two_active_listings_and_primary_stock(seed_context) -> None:
    settings, container, resolver, lifecycle = seed_context

    summary = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )

    assert summary["buyer_wallet"] == "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
    assert summary["owner_wallets"]["owner-2"] == "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65"
    assert summary["owner_wallets"]["owner-3"] == "0x90f79bf6eb2c4f870365e785982e1f101e93b906"

    assert summary["buyer_wallet"] == "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
    assert summary["owner_wallets"] == {
        "owner-1": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        "owner-2": "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65",
        "owner-3": "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
    }

    assert summary["primary_issuance_stock"] == PRIMARY_ISSUANCE_INITIAL_STOCK
    assert len(summary["machines"]) == 3
    assert len(summary["active_listings"]) == 2

    with container.session_factory() as session:
        machines = session.query(Machine).all()
        listings = session.query(MachineListing).all()
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        purchases = session.query(PrimaryIssuancePurchase).all()

    assert len(machines) == 3
    assert {machine.owner_user_id for machine in machines} == {"owner-1", "owner-2", "owner-3"}
    assert len(listings) == 2
    assert all(listing.state == "active" for listing in listings)
    assert sku is not None
    assert sku.stock_available == PRIMARY_ISSUANCE_INITIAL_STOCK
    assert purchases == []
    assert len(lifecycle.mint_calls) == 3


def test_seed_demo_projection_state_marks_exactly_one_unlisted_machine(seed_context) -> None:
    settings, container, resolver, lifecycle = seed_context

    summary = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )

    unlisted_machine_ids = [row["machine_id"] for row in summary["machines"] if not row["listed"]]
    listed_machine_ids = [row["machine_id"] for row in summary["machines"] if row["listed"]]

    assert unlisted_machine_ids == ["machine-owner-1"]
    assert sorted(listed_machine_ids) == ["machine-owner-2", "machine-owner-3"]


def test_format_seed_report_prints_owners_machines_listings_and_stock(seed_context) -> None:
    settings, container, resolver, lifecycle = seed_context

    summary = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )
    report = format_seed_report(
        seeded=summary,
        buyer_balances={
            "hsk": {"balance": 10_000 * 10**18, "tx": "already_funded"},
            "pwr": {"balance": 10_000 * 10**18, "tx": "0xpwr"},
            "usdc": {"balance": 10_000 * 10**6, "tx": "0xusdc"},
            "usdt": {"balance": 10_000 * 10**6, "tx": "0xusdt"},
        },
    )

    assert "- buyer_wallet=0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc" in report
    assert "  - owner-1: 0x70997970c51812dc3a010c7d01b50e0d17dc79c8" in report
    assert "  - owner-2: 0x15d34aaf54267db7d7c367839aaf71a00a2c6a65" in report
    assert "  - owner-3: 0x90f79bf6eb2c4f870365e785982e1f101e93b906" in report

    assert "owners=owner-1, owner-2, owner-3" in report
    assert "machine-owner-1 owner=owner-1" in report
    assert "machine-owner-2 owner=owner-2" in report
    assert "machine-owner-3 owner=owner-3" in report
    assert "active_secondary_market_listings:" in report
    assert "onchain_listing_id=2001 machine_id=machine-owner-2 owner=owner-2 price_units=1250000" in report
    assert "onchain_listing_id=2002 machine_id=machine-owner-3 owner=owner-3 price_units=1550000" in report
    assert "primary_issuance_stock=10" in report
    assert "buyer_balances:" in report
    assert "hsk_wei=10000000000000000000000" in report
    assert "pwr_wei=10000000000000000000000" in report
    assert "usdc_units=10000000000" in report
    assert "usdt_units=10000000000" in report
    assert "funding_txs:" in report
    assert "hsk=already_funded" in report
    assert "pwr=0xpwr" in report
    assert "usdc=0xusdc" in report
    assert "usdt=0xusdt" in report


def test_seed_demo_projection_state_is_idempotent_without_duplicate_rows(seed_context) -> None:
    settings, container, resolver, lifecycle = seed_context

    seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )
    seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )

    with container.session_factory() as session:
        machines = session.query(Machine).all()
        listings = session.query(MachineListing).all()

    assert len(machines) == 3
    assert len(listings) == 2
    assert len(lifecycle.mint_calls) == 3


def test_seed_demo_projection_state_uses_local_demo_wallet_fallback_for_owner2_owner3(seed_context) -> None:
    _, _, _, lifecycle = seed_context
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = json.dumps(
        {
            "buyer-1": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            "owner-1": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            "transferee-1": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
            "treasury-1": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        }
    )
    reset_settings_cache()
    reset_container_cache()
    settings = get_settings()
    container = get_container()
    resolver = _build_demo_wallet_resolver(settings=settings)

    summary = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )

    machine_wallets = {}
    with container.session_factory() as session:
        for machine in session.query(Machine).all():
            machine_wallets[machine.owner_user_id] = machine.owner_chain_address

    assert summary["owners"] == ["owner-1", "owner-2", "owner-3"]
    assert machine_wallets["owner-1"] == "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    assert machine_wallets["owner-2"] == "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65"
    assert machine_wallets["owner-3"] == "0x90f79bf6eb2c4f870365e785982e1f101e93b906"


def test_seed_demo_projection_state_uses_real_onchain_listing_ids_when_available(seed_context, monkeypatch) -> None:
    settings, container, resolver, lifecycle = seed_context
    created_listing_ids = iter(["11", "12"])

    monkeypatch.setattr(
        prepare_local_browser_demo,
        "_activate_demo_listing_onchain",
        lambda **_: next(created_listing_ids),
    )

    summary = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )

    assert [listing["onchain_listing_id"] for listing in summary["active_listings"]] == ["11", "12"]

    with container.session_factory() as session:
        listings = session.query(MachineListing).order_by(MachineListing.machine_id.asc()).all()

    assert [listing.onchain_listing_id for listing in listings] == ["11", "12"]
