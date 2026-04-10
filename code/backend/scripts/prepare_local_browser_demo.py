from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete
from web3 import Web3

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes.primary_issuance import (  # noqa: E402
    PRIMARY_ISSUANCE_CURRENCY,
    PRIMARY_ISSUANCE_DEFAULT_STOCK,
    PRIMARY_ISSUANCE_DISPLAY_NAME,
    PRIMARY_ISSUANCE_GPU_SPEC,
    PRIMARY_ISSUANCE_MODEL_FAMILY,
    PRIMARY_ISSUANCE_PRICE_CENTS,
    PRIMARY_ISSUANCE_PROFILE_LABEL,
    PRIMARY_ISSUANCE_SKU_ID,
)
from app.core.config import get_settings, reset_settings_cache  # noqa: E402
from app.core.container import get_container, reset_container_cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.domain.models import (  # noqa: E402
    Machine,
    MachineListing,
    PrimaryIssuancePurchase,
    PrimaryIssuanceSku,
    utc_now,
)
from app.integrations.buyer_address_resolver import BuyerAddressResolver  # noqa: E402
from app.onchain.lifecycle_service import (  # noqa: E402
    OnchainLifecycleService,
    reset_onchain_lifecycle_service_cache,
)

TARGET_BUYER_USER_ID = "buyer-1"
TARGET_BUYER_PWR = 100 * 10**18
PRIMARY_ISSUANCE_INITIAL_STOCK = PRIMARY_ISSUANCE_DEFAULT_STOCK
LOCAL_DEMO_WALLET_FALLBACKS = {
    "buyer-1": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
    "owner-1": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "owner-2": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
    "owner-3": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
}


@dataclass(frozen=True)
class DemoMachineSeed:
    machine_id: str
    display_name: str
    owner_user_id: str


@dataclass(frozen=True)
class DemoListingSeed:
    listing_id: str
    onchain_listing_id: str
    machine_id: str
    owner_user_id: str
    price_units: int


DEMO_MACHINE_SEEDS = (
    DemoMachineSeed(
        machine_id="machine-owner-1",
        display_name="OutcomeX Owner-1 Qwen Rack",
        owner_user_id="owner-1",
    ),
    DemoMachineSeed(
        machine_id="machine-owner-2",
        display_name="OutcomeX Owner-2 Qwen Rack",
        owner_user_id="owner-2",
    ),
    DemoMachineSeed(
        machine_id="machine-owner-3",
        display_name="OutcomeX Owner-3 Qwen Rack",
        owner_user_id="owner-3",
    ),
)

DEMO_ACTIVE_LISTING_SEEDS = (
    DemoListingSeed(
        listing_id="listing-owner-2",
        onchain_listing_id="2001",
        machine_id="machine-owner-2",
        owner_user_id="owner-2",
        price_units=1_250_000,
    ),
    DemoListingSeed(
        listing_id="listing-owner-3",
        onchain_listing_id="2002",
        machine_id="machine-owner-3",
        owner_user_id="owner-3",
        price_units=1_550_000,
    ),
)

PWR_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


def _web3(rpc_url: str) -> Web3:
    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    if not web3.is_connected():
        raise RuntimeError(f"rpc_unreachable:{rpc_url}")
    return web3


def _fund_buyer_with_pwr(*, web3: Web3, settings, resolver: BuyerAddressResolver) -> tuple[str, int]:
    buyer = resolver.resolve_wallet(TARGET_BUYER_USER_ID)
    if buyer is None:
        raise RuntimeError("buyer_wallet_unresolved")
    admin_key = settings.onchain_broadcaster_private_key.strip()
    if not admin_key:
        raise RuntimeError("admin_private_key_missing")

    contract = web3.eth.contract(
        address=Web3.to_checksum_address(settings.onchain_pwr_token_address),
        abi=PWR_ABI,
    )
    current_balance = int(contract.functions.balanceOf(Web3.to_checksum_address(buyer)).call())
    if current_balance >= TARGET_BUYER_PWR:
        return "already_funded", current_balance

    admin = web3.eth.account.from_key(admin_key)
    tx = contract.functions.transfer(
        Web3.to_checksum_address(buyer),
        TARGET_BUYER_PWR - current_balance,
    ).build_transaction(
        {
            "from": admin.address,
            "nonce": web3.eth.get_transaction_count(admin.address, "pending"),
            "chainId": settings.onchain_chain_id,
            "gasPrice": web3.eth.gas_price,
        }
    )
    tx["gas"] = web3.eth.estimate_gas(tx)
    signed = admin.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=max(15, int(settings.onchain_tx_timeout_seconds)))
    if receipt.status != 1:
        raise RuntimeError(f"pwr_funding_failed:{tx_hash.hex()}")
    new_balance = int(contract.functions.balanceOf(Web3.to_checksum_address(buyer)).call())
    return tx_hash.hex(), new_balance


def _resolve_wallet_or_raise(*, resolver: BuyerAddressResolver, user_id: str) -> str:
    wallet = resolver.resolve_wallet(user_id)
    if wallet is None:
        wallet = LOCAL_DEMO_WALLET_FALLBACKS.get(user_id)
    if wallet is None:
        raise RuntimeError(f"wallet_unresolved:{user_id}")
    return wallet.lower()


def _build_demo_wallet_resolver(*, settings) -> BuyerAddressResolver:
    parsed = json.loads(settings.buyer_wallet_map_json or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("buyer_wallet_map_invalid")
    wallet_map = {str(user_id): str(wallet) for user_id, wallet in parsed.items()}
    wallet_map.setdefault("buyer-1", LOCAL_DEMO_WALLET_FALLBACKS["buyer-1"])
    wallet_map.setdefault("owner-1", LOCAL_DEMO_WALLET_FALLBACKS["owner-1"])
    wallet_map.setdefault("owner-2", wallet_map.get("transferee-1", LOCAL_DEMO_WALLET_FALLBACKS["owner-2"]))
    wallet_map.setdefault("owner-3", wallet_map.get("treasury-1", LOCAL_DEMO_WALLET_FALLBACKS["owner-3"]))
    return BuyerAddressResolver(wallet_map)


def _ensure_machine_record(
    *,
    seed: DemoMachineSeed,
    resolver: BuyerAddressResolver,
    lifecycle: OnchainLifecycleService,
    session,
) -> Machine:
    owner_wallet = _resolve_wallet_or_raise(resolver=resolver, user_id=seed.owner_user_id)
    machine = session.get(Machine, seed.machine_id)

    if machine is None or machine.onchain_machine_id is None:
        minted = lifecycle.mint_machine_for_owner(
            owner_user_id=seed.owner_user_id,
            token_uri=f"ipfs://outcomex-machine/local-browser-demo/{seed.machine_id}",
        )
        if not minted.onchain_machine_id:
            raise RuntimeError(f"machine_mint_missing_id:{seed.machine_id}:{minted.tx_hash}")
        onchain_machine_id = minted.onchain_machine_id
    else:
        onchain_machine_id = machine.onchain_machine_id

    if machine is None:
        machine = Machine(
            id=seed.machine_id,
            display_name=seed.display_name,
            owner_user_id=seed.owner_user_id,
            owner_chain_address=owner_wallet,
            ownership_source="chain",
            onchain_machine_id=onchain_machine_id,
        )
    else:
        machine.display_name = seed.display_name
        machine.owner_user_id = seed.owner_user_id
        machine.owner_chain_address = owner_wallet
        machine.ownership_source = "chain"
        machine.onchain_machine_id = onchain_machine_id
    session.add(machine)
    session.flush()
    return machine


def _seed_primary_issuance_stock(*, session) -> int:
    session.execute(delete(PrimaryIssuancePurchase))

    sku = session.get(PrimaryIssuanceSku, PRIMARY_ISSUANCE_SKU_ID)
    if sku is None:
        sku = PrimaryIssuanceSku(
            sku_id=PRIMARY_ISSUANCE_SKU_ID,
            display_name=PRIMARY_ISSUANCE_DISPLAY_NAME,
            profile_label=PRIMARY_ISSUANCE_PROFILE_LABEL,
            gpu_spec=PRIMARY_ISSUANCE_GPU_SPEC,
            model_family=PRIMARY_ISSUANCE_MODEL_FAMILY,
            price_cents=PRIMARY_ISSUANCE_PRICE_CENTS,
            currency=PRIMARY_ISSUANCE_CURRENCY,
            stock_available=PRIMARY_ISSUANCE_INITIAL_STOCK,
        )
    else:
        sku.display_name = PRIMARY_ISSUANCE_DISPLAY_NAME
        sku.profile_label = PRIMARY_ISSUANCE_PROFILE_LABEL
        sku.gpu_spec = PRIMARY_ISSUANCE_GPU_SPEC
        sku.model_family = PRIMARY_ISSUANCE_MODEL_FAMILY
        sku.price_cents = PRIMARY_ISSUANCE_PRICE_CENTS
        sku.currency = PRIMARY_ISSUANCE_CURRENCY
        sku.stock_available = PRIMARY_ISSUANCE_INITIAL_STOCK
    session.add(sku)
    session.flush()
    return sku.stock_available


def _seed_demo_listings(*, settings, resolver: BuyerAddressResolver, machines_by_id: dict[str, Machine], session) -> list[dict[str, object]]:
    session.execute(delete(MachineListing))
    listed_at = utc_now()
    expires_at = listed_at + timedelta(days=30)
    listings: list[dict[str, object]] = []

    for listing_seed in DEMO_ACTIVE_LISTING_SEEDS:
        machine = machines_by_id[listing_seed.machine_id]
        seller_wallet = _resolve_wallet_or_raise(resolver=resolver, user_id=listing_seed.owner_user_id)
        listing = MachineListing(
            id=listing_seed.listing_id,
            onchain_listing_id=listing_seed.onchain_listing_id,
            machine_id=machine.id,
            onchain_machine_id=machine.onchain_machine_id,
            seller_chain_address=seller_wallet,
            buyer_chain_address=None,
            payment_token_address=settings.onchain_usdc_address.lower(),
            payment_token_symbol="USDC",
            payment_token_decimals=6,
            price_units=listing_seed.price_units,
            state="active",
            listed_at=listed_at,
            expires_at=expires_at,
            cancelled_at=None,
            filled_at=None,
        )
        session.add(listing)
        listings.append(
            {
                "onchain_listing_id": listing.onchain_listing_id,
                "machine_id": machine.id,
                "owner_user_id": listing_seed.owner_user_id,
                "price_units": listing_seed.price_units,
            }
        )

    session.flush()
    return listings


def seed_demo_projection_state(*, settings, container, resolver: BuyerAddressResolver, lifecycle: OnchainLifecycleService) -> dict[str, object]:
    Base.metadata.create_all(bind=container.engine)

    buyer_wallet = _resolve_wallet_or_raise(resolver=resolver, user_id=TARGET_BUYER_USER_ID)
    owner_wallets: dict[str, str] = {}
    for seed in DEMO_MACHINE_SEEDS:
        owner_wallets[seed.owner_user_id] = _resolve_wallet_or_raise(
            resolver=resolver,
            user_id=seed.owner_user_id,
        )

    with container.session_factory() as session:
        machines_by_id: dict[str, Machine] = {}
        machine_rows: list[dict[str, object]] = []
        listed_machine_ids = {listing.machine_id for listing in DEMO_ACTIVE_LISTING_SEEDS}

        for machine_seed in DEMO_MACHINE_SEEDS:
            machine = _ensure_machine_record(
                seed=machine_seed,
                resolver=resolver,
                lifecycle=lifecycle,
                session=session,
            )
            machines_by_id[machine.id] = machine
            machine_rows.append(
                {
                    "machine_id": machine.id,
                    "onchain_machine_id": machine.onchain_machine_id,
                    "owner_user_id": machine.owner_user_id,
                    "listed": machine.id in listed_machine_ids,
                }
            )

        active_listings = _seed_demo_listings(
            settings=settings,
            resolver=resolver,
            machines_by_id=machines_by_id,
            session=session,
        )
        primary_stock = _seed_primary_issuance_stock(session=session)
        session.commit()

    return {
        "buyer_user_id": TARGET_BUYER_USER_ID,
        "buyer_wallet": buyer_wallet,
        "owners": [seed.owner_user_id for seed in DEMO_MACHINE_SEEDS],
        "owner_wallets": owner_wallets,
        "machines": machine_rows,
        "active_listings": active_listings,
        "primary_issuance_stock": primary_stock,
    }


def format_seed_report(*, seeded: dict[str, object], buyer_balance: int, funding_tx: str) -> str:
    lines = [
        "Prepared local browser demo:",
        f"- buyer_user_id={seeded['buyer_user_id']}",
        f"- buyer_wallet={seeded['buyer_wallet']}",
        f"- owners={', '.join(seeded['owners'])}",
        "- owner_wallets:",
    ]
    for owner_id, wallet in seeded["owner_wallets"].items():
        lines.append(f"  - {owner_id}: {wallet}")
    lines.append("- machines:")
    for machine in seeded["machines"]:
        listing_state = "listed" if machine["listed"] else "unlisted"
        lines.append(
            f"  - {machine['machine_id']} owner={machine['owner_user_id']} "
            f"onchain_machine_id={machine['onchain_machine_id']} state={listing_state}"
        )
    lines.append("- active_secondary_market_listings:")
    for listing in seeded["active_listings"]:
        lines.append(
            f"  - onchain_listing_id={listing['onchain_listing_id']} "
            f"machine_id={listing['machine_id']} owner={listing['owner_user_id']} "
            f"price_units={listing['price_units']}"
        )
    lines.append(f"- primary_issuance_stock={seeded['primary_issuance_stock']}")
    lines.append(f"- buyer_pwr_balance_wei={buyer_balance}")
    lines.append(f"- pwr_funding_tx={funding_tx}")
    return "\n".join(lines)


def main() -> None:
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()

    settings = get_settings()
    resolver = _build_demo_wallet_resolver(settings=settings)
    container = get_container()
    lifecycle = OnchainLifecycleService(settings=settings, buyer_address_resolver=resolver)
    web3 = _web3(settings.onchain_rpc_url)

    funding_tx, buyer_balance = _fund_buyer_with_pwr(web3=web3, settings=settings, resolver=resolver)
    seeded = seed_demo_projection_state(
        settings=settings,
        container=container,
        resolver=resolver,
        lifecycle=lifecycle,
    )
    print(format_seed_report(seeded=seeded, buyer_balance=buyer_balance, funding_tx=funding_tx))


if __name__ == "__main__":
    main()
