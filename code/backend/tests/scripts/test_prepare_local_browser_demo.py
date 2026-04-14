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
            "buyer": "0xd9180752dfdC003Fa5bD2a4bb9b0Ead2E2149CdB",
            "owner-1": "0x0A4401376B024E72cA9481192c88F4d4eb80cDf8",
            "owner-2": "0x1feDb8e927b9A1c9878c8C9e0beA518Fc96A9265",
        }
    )
    os.environ["OUTCOMEX_ONCHAIN_USDT_ADDRESS"] = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"

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

    assert summary["owner_wallets"] == {
        "owner-1": "0x0a4401376b024e72ca9481192c88f4d4eb80cdf8",
        "owner-2": "0x1fedb8e927b9a1c9878c8c9e0bea518fc96a9265",
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
    assert {machine.owner_user_id for machine in machines} == {"owner-1", "owner-2"}
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
        funded_balances={
            "owner-1": {
                "hsk": {"balance": 10 * 10**18, "tx": "already_funded"},
                "pwr": {"balance": 10_000 * 10**18, "tx": "0xpwr1"},
                "usdt": {"balance": 100 * 10**6, "tx": "0xusdt1"},
            },
            "owner-2": {
                "hsk": {"balance": 10 * 10**18, "tx": "already_funded"},
                "pwr": {"balance": 10_000 * 10**18, "tx": "0xpwr2"},
                "usdt": {"balance": 100 * 10**6, "tx": "0xusdt2"},
            },
            "buyer": {
                "hsk": {"balance": 10 * 10**18, "tx": "already_funded"},
                "pwr": {"balance": 10_000 * 10**18, "tx": "0xpwr3"},
                "usdt": {"balance": 100 * 10**6, "tx": "0xusdt3"},
            },
        },
    )

    assert "  - owner-1: 0x0a4401376b024e72ca9481192c88f4d4eb80cdf8" in report
    assert "  - owner-2: 0x1fedb8e927b9a1c9878c8c9e0bea518fc96a9265" in report

    assert "owners=owner-1, owner-2" in report
    assert "machine-owner-1 owner=owner-1" in report
    assert "machine-owner-2 owner=owner-2" in report
    assert "machine-owner-3 owner=owner-2" in report
    assert "active_secondary_market_listings:" in report
    assert "onchain_listing_id=2001 machine_id=machine-owner-2 owner=owner-2 price_units=1250000" in report
    assert "onchain_listing_id=2002 machine_id=machine-owner-3 owner=owner-2 price_units=1550000" in report
    assert "primary_issuance_stock=10" in report
    assert "funded_wallet_balances:" in report
    assert "hsk_wei=10000000000000000000" in report
    assert "pwr_wei=10000000000000000000000" in report
    assert "usdt_units=100000000" in report
    assert "funding_txs:" in report
    assert "hsk=already_funded" in report
    assert "pwr=0xpwr1" in report
    assert "pwr=0xpwr2" in report
    assert "pwr=0xpwr3" in report
    assert "usdt=0xusdt1" in report
    assert "usdt=0xusdt2" in report
    assert "usdt=0xusdt3" in report


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
            "buyer": "0xd9180752dfdC003Fa5bD2a4bb9b0Ead2E2149CdB",
            "owner-1": "0x0A4401376B024E72cA9481192c88F4d4eb80cDf8",
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

    assert summary["owners"] == ["owner-1", "owner-2"]
    assert machine_wallets["owner-1"] == "0x0a4401376b024e72ca9481192c88f4d4eb80cdf8"
    assert machine_wallets["owner-2"] == "0x1fedb8e927b9a1c9878c8c9e0bea518fc96a9265"


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
