from __future__ import annotations

import sys
from pathlib import Path

from web3 import Web3

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings, reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.db.base import Base
from app.domain.models import Machine
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.onchain.lifecycle_service import get_onchain_lifecycle_service, reset_onchain_lifecycle_service_cache

TARGET_MACHINE_ID = "machine-1"
TARGET_MACHINE_NAME = "OutcomeX Local Qwen Rack"
TARGET_OWNER_USER_ID = "owner-1"
TARGET_BUYER_USER_ID = "buyer-1"
TARGET_BUYER_PWR = 100 * 10**18
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


def _ensure_machine(*, settings, resolver: BuyerAddressResolver) -> tuple[str, str]:
    owner_wallet = resolver.resolve_wallet(TARGET_OWNER_USER_ID)
    if owner_wallet is None:
        raise RuntimeError("owner_wallet_unresolved")

    lifecycle = get_onchain_lifecycle_service()
    container = get_container()
    Base.metadata.create_all(bind=container.engine)

    with container.session_factory() as session:
        machine = session.get(Machine, TARGET_MACHINE_ID)
        if machine is not None:
            machine.display_name = TARGET_MACHINE_NAME
            machine.owner_user_id = TARGET_OWNER_USER_ID
            machine.owner_chain_address = owner_wallet.lower()
            machine.ownership_source = "chain"
            session.add(machine)
            session.commit()
            return machine.onchain_machine_id or "", machine.id

    minted = lifecycle.mint_machine_for_owner(
        owner_user_id=TARGET_OWNER_USER_ID,
        token_uri="ipfs://outcomex-machine/local-browser-demo",
    )
    if not minted.onchain_machine_id:
        raise RuntimeError(f"machine_mint_missing_id:{minted.tx_hash}")

    with container.session_factory() as session:
        machine = session.get(Machine, TARGET_MACHINE_ID)
        if machine is None:
            machine = Machine(
                id=TARGET_MACHINE_ID,
                display_name=TARGET_MACHINE_NAME,
                owner_user_id=TARGET_OWNER_USER_ID,
                owner_chain_address=owner_wallet.lower(),
                ownership_source="chain",
                onchain_machine_id=minted.onchain_machine_id,
            )
        else:
            machine.display_name = TARGET_MACHINE_NAME
            machine.owner_user_id = TARGET_OWNER_USER_ID
            machine.owner_chain_address = owner_wallet.lower()
            machine.ownership_source = "chain"
            machine.onchain_machine_id = minted.onchain_machine_id
        session.add(machine)
        session.commit()
    return minted.onchain_machine_id, TARGET_MACHINE_ID


def main() -> None:
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()
    settings = get_settings()
    resolver = BuyerAddressResolver.from_json(settings.buyer_wallet_map_json)
    web3 = _web3(settings.onchain_rpc_url)

    funding_tx, buyer_balance = _fund_buyer_with_pwr(web3=web3, settings=settings, resolver=resolver)
    onchain_machine_id, machine_id = _ensure_machine(settings=settings, resolver=resolver)

    print("Prepared local browser demo:")
    print(f"- machine_id={machine_id}")
    print(f"- onchain_machine_id={onchain_machine_id}")
    print(f"- buyer_user_id={TARGET_BUYER_USER_ID}")
    print(f"- owner_user_id={TARGET_OWNER_USER_ID}")
    print(f"- buyer_pwr_balance_wei={buyer_balance}")
    print(f"- pwr_funding_tx={funding_tx}")


if __name__ == "__main__":
    main()
