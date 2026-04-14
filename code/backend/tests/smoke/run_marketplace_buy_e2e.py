#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from eth_account import Account
from fastapi.testclient import TestClient
from web3 import HTTPProvider, Web3

Account.enable_unaudited_hdwallet_features()

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "code" / "backend"
CONTRACTS_ROOT = REPO_ROOT / "code" / "contracts"
BROADCAST_RUN_PATH = CONTRACTS_ROOT / "broadcast" / "DeployLocal.s.sol" / "133" / "runWithConfig-latest.json"

DEFAULT_RPC_URL = os.getenv("OUTCOMEX_MARKETPLACE_E2E_RPC_URL", "http://127.0.0.1:8545")
DEFAULT_CHAIN_ID = int(os.getenv("OUTCOMEX_MARKETPLACE_E2E_CHAIN_ID", "133"))
DEFAULT_MNEMONIC = os.getenv(
    "OUTCOMEX_MARKETPLACE_E2E_MNEMONIC",
    "test test test test test test test test test test test junk",
)
DEFAULT_DB_PATH = Path(os.getenv("OUTCOMEX_MARKETPLACE_E2E_DB_PATH", "/tmp/outcomex-marketplace-e2e.sqlite"))
DEFAULT_OUTPUT_ROOT = Path(
    os.getenv("OUTCOMEX_MARKETPLACE_E2E_OUTPUT_ROOT", "/tmp/outcomex-marketplace-e2e-output")
)
DEFAULT_REPORT_PATH = Path(
    os.getenv("OUTCOMEX_MARKETPLACE_E2E_REPORT_PATH", "/tmp/outcomex-marketplace-e2e-report.json")
)

ERC20_ABI = [
    {
        "name": "transfer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

ERC721_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [],
    },
]

MARKETPLACE_ABI = [
    {
        "name": "createListing",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "machineId", "type": "uint256"},
            {"name": "paymentToken", "type": "address"},
            {"name": "price", "type": "uint256"},
            {"name": "expiry", "type": "uint64"},
        ],
        "outputs": [{"name": "listingId", "type": "uint256"}],
    },
    {
        "name": "buyListing",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "listingId", "type": "uint256"}],
        "outputs": [],
    },
]


@dataclass(frozen=True)
class LocalAccount:
    user_id: str
    address: str
    private_key: str


@dataclass(frozen=True)
class Deployment:
    usdc: str
    usdt: str
    pwr: str
    machine_asset: str
    machine_marketplace: str
    revenue_vault: str
    settlement_controller: str
    order_book: str
    order_payment_router: str
    sample_machine_id: int


class _ManualIndexerStatus:
    enabled = False
    reason = "manual_poll_in_marketplace_e2e"


class _ManualIndexerProxy:
    status = _ManualIndexerStatus()

    def poll_once(self) -> None:
        return None

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        settlement_id
        return None


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    import requests

    response = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error") is not None:
        raise RuntimeError(f"json_rpc_error:{payload['error']}")
    return payload.get("result")


def _derive_account(index: int, *, user_id: str) -> LocalAccount:
    account = Account.from_mnemonic(DEFAULT_MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")
    return LocalAccount(user_id=user_id, address=account.address, private_key=account.key.hex())


def _wait_for_rpc(rpc_url: str, *, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if _rpc_call(rpc_url, "eth_chainId", []):
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"rpc_not_ready:{rpc_url}")


def _ensure_fresh_paths() -> None:
    for path in (DEFAULT_DB_PATH, DEFAULT_REPORT_PATH):
        if path.exists():
            path.unlink()
    if DEFAULT_OUTPUT_ROOT.exists():
        shutil.rmtree(DEFAULT_OUTPUT_ROOT, ignore_errors=True)
    DEFAULT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    DEFAULT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _start_anvil_if_needed(rpc_url: str) -> subprocess.Popen[str] | None:
    try:
        _wait_for_rpc(rpc_url, timeout_seconds=1.5)
        return None
    except Exception:
        pass

    parsed_port = DEFAULT_RPC_URL.rsplit(":", 1)[-1]
    command = [
        "anvil",
        "--host",
        "127.0.0.1",
        "--port",
        parsed_port,
        "--chain-id",
        str(DEFAULT_CHAIN_ID),
        "--mnemonic",
        DEFAULT_MNEMONIC,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_for_rpc(rpc_url, timeout_seconds=20.0)
    except Exception:
        output = ""
        if process.stdout is not None:
            output = process.stdout.read()
        process.kill()
        raise RuntimeError(f"anvil_start_failed:{output}")
    return process


def _deploy_contracts(*, rpc_url: str, admin: LocalAccount, treasury: LocalAccount, machine_owner: LocalAccount) -> Deployment:
    command = [
        "forge",
        "script",
        "script/DeployLocal.s.sol:DeployLocal",
        "--rpc-url",
        rpc_url,
        "--private-key",
        admin.private_key,
        "--broadcast",
        "--sig",
        "runWithConfig(address,address,address)",
        admin.address,
        treasury.address,
        machine_owner.address,
        "-vvvv",
    ]
    subprocess.run(command, cwd=CONTRACTS_ROOT, check=True, capture_output=True, text=True)
    payload = json.loads(BROADCAST_RUN_PATH.read_text(encoding="utf-8"))
    addresses: dict[str, str] = {}
    for tx in payload.get("transactions", []):
        if tx.get("transactionType") == "CREATE":
            contract_name = str(tx.get("contractName"))
            contract_address = tx.get("contractAddress")
            if contract_name and contract_address:
                addresses[contract_name] = Web3.to_checksum_address(contract_address)

    required = (
        "MockUSDCWithAuthorization",
        "MockUSDT",
        "PWRToken",
        "MachineAssetNFT",
        "MachineMarketplace",
        "RevenueVault",
        "SettlementController",
        "OrderBook",
        "OrderPaymentRouter",
    )
    if not all(name in addresses for name in required):
        raise RuntimeError(f"deployment_addresses_incomplete:{sorted(addresses.keys())}")

    raw_return = str(payload.get("returns", {}).get("deployed", {}).get("value", ""))
    pieces = [part.strip() for part in raw_return.strip("() ").split(",") if part.strip()]
    if len(pieces) < 10:
        raise RuntimeError(f"deployment_return_unexpected:{raw_return}")

    return Deployment(
        usdc=addresses["MockUSDCWithAuthorization"],
        usdt=addresses["MockUSDT"],
        pwr=addresses["PWRToken"],
        machine_asset=addresses["MachineAssetNFT"],
        machine_marketplace=addresses["MachineMarketplace"],
        revenue_vault=addresses["RevenueVault"],
        settlement_controller=addresses["SettlementController"],
        order_book=addresses["OrderBook"],
        order_payment_router=addresses["OrderPaymentRouter"],
        sample_machine_id=int(pieces[-1]),
    )


def _configure_backend_env(
    *,
    deployment: Deployment,
    admin: LocalAccount,
    buyer: LocalAccount,
    owner: LocalAccount,
    treasury: LocalAccount,
) -> None:
    buyer_wallet_map = {
        buyer.user_id: buyer.address,
        owner.user_id: owner.address,
        treasury.user_id: treasury.address,
    }
    os.environ.update(
        {
            "OUTCOMEX_ENV": "dev",
            "OUTCOMEX_DATABASE_URL": f"sqlite+pysqlite:///{DEFAULT_DB_PATH.as_posix()}",
            "OUTCOMEX_AUTO_CREATE_TABLES": "true",
            "OUTCOMEX_ONCHAIN_RPC_URL": DEFAULT_RPC_URL,
            "OUTCOMEX_ONCHAIN_CHAIN_ID": str(DEFAULT_CHAIN_ID),
            "OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS": deployment.machine_asset,
            "OUTCOMEX_ONCHAIN_MACHINE_MARKETPLACE_ADDRESS": deployment.machine_marketplace,
            "OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS": deployment.order_book,
            "OUTCOMEX_ONCHAIN_ORDER_PAYMENT_ROUTER_ADDRESS": deployment.order_payment_router,
            "OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS": deployment.settlement_controller,
            "OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS": deployment.revenue_vault,
            "OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS": deployment.pwr,
            "OUTCOMEX_ONCHAIN_USDC_ADDRESS": deployment.usdc,
            "OUTCOMEX_ONCHAIN_USDT_ADDRESS": deployment.usdt,
            "OUTCOMEX_ONCHAIN_BROADCASTER_PRIVATE_KEY": admin.private_key,
            "OUTCOMEX_ONCHAIN_ADAPTER_PRIVATE_KEY": admin.private_key,
            "OUTCOMEX_ONCHAIN_PLATFORM_TREASURY_PRIVATE_KEY": treasury.private_key,
            "OUTCOMEX_ONCHAIN_MACHINE_OWNER_PRIVATE_KEY": owner.private_key,
            "OUTCOMEX_ONCHAIN_BUYER_PRIVATE_KEY": buyer.private_key,
            "OUTCOMEX_BUYER_WALLET_MAP_JSON": json.dumps(buyer_wallet_map),
            "OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT": str(DEFAULT_OUTPUT_ROOT),
            "OUTCOMEX_ONCHAIN_INDEXER_ENABLED": "true",
            "OUTCOMEX_ONCHAIN_INDEXER_POLL_SECONDS": "0.2",
            "OUTCOMEX_ONCHAIN_INDEXER_CONFIRMATION_DEPTH": "0",
            "OUTCOMEX_EXECUTION_SYNC_ENABLED": "false",
        }
    )


def _import_backend_modules() -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.core.config import reset_settings_cache
    from app.core.container import get_container, reset_container_cache
    from app.main import create_app
    from app.onchain.lifecycle_service import reset_onchain_lifecycle_service_cache

    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()
    return {
        "create_app": create_app,
        "get_container": get_container,
    }


def _web3() -> Web3:
    provider = HTTPProvider(DEFAULT_RPC_URL, request_kwargs={"timeout": 30})
    web3 = Web3(provider)
    if not web3.is_connected():
        raise RuntimeError("web3_not_connected")
    return web3


def _send_transaction(*, web3: Web3, private_key: str, to: str, data: str) -> dict[str, Any]:
    account = Account.from_key(private_key)
    tx = {
        "chainId": DEFAULT_CHAIN_ID,
        "from": account.address,
        "to": Web3.to_checksum_address(to),
        "data": data,
        "nonce": web3.eth.get_transaction_count(account.address, "pending"),
        "gasPrice": web3.eth.gas_price,
        "value": 0,
        "gas": 800_000,
    }
    tx["gas"] = int(web3.eth.estimate_gas(tx))
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    return {
        "tx_hash": f"0x{tx_hash.hex()}",
        "status": int(receipt.status),
        "block_number": int(receipt.blockNumber),
        "from": account.address,
        "to": Web3.to_checksum_address(to),
    }


def _contract_calldata(*, web3: Web3, contract_address: str, abi: list[dict[str, Any]], fn_name: str, args: list[Any]) -> str:
    contract = web3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    return contract.get_function_by_name(fn_name)(*args)._encode_transaction_data()


def _wait_until(label: str, predicate: Callable[[], Any], *, poller: Callable[[], None], timeout_seconds: float = 20.0) -> Any:
    deadline = time.time() + timeout_seconds
    last_value: Any = None
    while time.time() < deadline:
        poller()
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(0.2)
    raise RuntimeError(f"timeout_waiting_for:{label}:last={last_value!r}")


def _approve_erc20_max(*, web3: Web3, token_address: str, owner_private_key: str, spender: str) -> dict[str, Any]:
    calldata = _contract_calldata(
        web3=web3,
        contract_address=token_address,
        abi=ERC20_ABI,
        fn_name="approve",
        args=[spender, (2**256) - 1],
    )
    return _send_transaction(web3=web3, private_key=owner_private_key, to=token_address, data=calldata)


def _transfer_erc20(*, web3: Web3, token_address: str, sender_private_key: str, to: str, amount: int) -> dict[str, Any]:
    calldata = _contract_calldata(
        web3=web3,
        contract_address=token_address,
        abi=ERC20_ABI,
        fn_name="transfer",
        args=[Web3.to_checksum_address(to), amount],
    )
    return _send_transaction(web3=web3, private_key=sender_private_key, to=token_address, data=calldata)


def main() -> None:
    report: dict[str, Any] = {
        "scenario": "marketplace_buy_projection",
        "report_path": str(DEFAULT_REPORT_PATH),
        "db_path": str(DEFAULT_DB_PATH),
        "output_root": str(DEFAULT_OUTPUT_ROOT),
        "api_snapshots": {},
        "transactions": {},
        "final_checks": {},
    }

    anvil_process: subprocess.Popen[str] | None = None
    try:
        _ensure_fresh_paths()
        anvil_process = _start_anvil_if_needed(DEFAULT_RPC_URL)
        admin = _derive_account(0, user_id="admin")
        treasury = _derive_account(1, user_id="treasury")
        seller = _derive_account(2, user_id="seller-1")
        buyer = _derive_account(3, user_id="buyer-1")

        deployment = _deploy_contracts(rpc_url=DEFAULT_RPC_URL, admin=admin, treasury=treasury, machine_owner=seller)
        _configure_backend_env(
            deployment=deployment,
            admin=admin,
            buyer=buyer,
            owner=seller,
            treasury=treasury,
        )
        report["deployment"] = {
            "machine_asset": deployment.machine_asset,
            "machine_marketplace": deployment.machine_marketplace,
            "usdc": deployment.usdc,
            "sample_machine_id": deployment.sample_machine_id,
        }
        report["accounts"] = {
            "seller": {"user_id": seller.user_id, "address": seller.address},
            "buyer": {"user_id": buyer.user_id, "address": buyer.address},
        }

        modules = _import_backend_modules()
        app = modules["create_app"]()
        container = modules["get_container"]()
        web3 = _web3()
        real_indexer = container.onchain_indexer
        container.onchain_indexer = _ManualIndexerProxy()

        if not getattr(real_indexer, "status", None) or not real_indexer.status.enabled:
            raise RuntimeError(f"indexer_not_live:{getattr(real_indexer, 'status', None)}")

        def poll_indexer() -> None:
            outcome = real_indexer.poll_once()
            if outcome is not None:
                report.setdefault("indexer_polls", []).append(
                    {
                        "from_block": outcome.from_block,
                        "to_block": outcome.to_block,
                        "applied": outcome.applied_events,
                        "duplicates": outcome.skipped_duplicates,
                        "last_scanned_block": outcome.cursor_advanced_to,
                        "rewind": outcome.reorg_detected,
                    }
                )

        with TestClient(app) as client:
            client.post("/api/v1/debug/smoke-reset").raise_for_status()

            machine = client.post(
                "/api/v1/machines",
                json={
                    "owner_user_id": seller.user_id,
                    "display_name": "OutcomeX Marketplace Machine",
                },
            ).json()
            machine_id = machine["id"]
            onchain_machine_id = int(machine["onchain_machine_id"])

            projected_machine = _wait_until(
                "machine minted projection",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id and item["owner_chain_address"] == seller.address.lower()
                    ),
                    None,
                ),
                poller=poll_indexer,
            )

            report["api_snapshots"]["machine_after_mint"] = projected_machine

            approve_marketplace_tx = _send_transaction(
                web3=web3,
                private_key=seller.private_key,
                to=deployment.machine_asset,
                data=_contract_calldata(
                    web3=web3,
                    contract_address=deployment.machine_asset,
                    abi=ERC721_ABI,
                    fn_name="approve",
                    args=[Web3.to_checksum_address(deployment.machine_marketplace), onchain_machine_id],
                ),
            )

            listing_price_units = 1_250_000
            create_listing_tx = _send_transaction(
                web3=web3,
                private_key=seller.private_key,
                to=deployment.machine_marketplace,
                data=_contract_calldata(
                    web3=web3,
                    contract_address=deployment.machine_marketplace,
                    abi=MARKETPLACE_ABI,
                    fn_name="createListing",
                    args=[
                        onchain_machine_id,
                        Web3.to_checksum_address(deployment.usdc),
                        listing_price_units,
                        int(time.time()) + 3600,
                    ],
                ),
            )

            active_listing = _wait_until(
                "active marketplace listing projection",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/marketplace/listings").json()
                        if item["machine_id"] == machine_id and item["state"] == "active"
                    ),
                    None,
                ),
                poller=poll_indexer,
            )

            report["api_snapshots"]["before_buy"] = {
                "machines": client.get("/api/v1/machines").json(),
                "marketplace_listings": client.get("/api/v1/marketplace/listings").json(),
            }

            funding_tx = _transfer_erc20(
                web3=web3,
                token_address=deployment.usdc,
                sender_private_key=admin.private_key,
                to=buyer.address,
                amount=5_000_000,
            )
            approve_usdc_tx = _approve_erc20_max(
                web3=web3,
                token_address=deployment.usdc,
                owner_private_key=buyer.private_key,
                spender=deployment.machine_marketplace,
            )
            buy_listing_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=deployment.machine_marketplace,
                data=_contract_calldata(
                    web3=web3,
                    contract_address=deployment.machine_marketplace,
                    abi=MARKETPLACE_ABI,
                    fn_name="buyListing",
                    args=[int(active_listing["onchain_listing_id"])],
                ),
            )

            machine_after_buy = _wait_until(
                "buyer owner projection after marketplace buy",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id
                        and item["owner_chain_address"] == buyer.address.lower()
                        and item["owner_user_id"] == buyer.user_id
                        and item["active_listing"] is None
                    ),
                    None,
                ),
                poller=poll_indexer,
            )

            listings_after_buy_marker = _wait_until(
                "listing cleared after marketplace buy",
                lambda: (
                    {"listings": client.get("/api/v1/marketplace/listings").json()}
                    if not any(
                        item["machine_id"] == machine_id
                        for item in client.get("/api/v1/marketplace/listings").json()
                    )
                    else None
                ),
                poller=poll_indexer,
            )
            listings_after_buy = listings_after_buy_marker["listings"]

            report["transactions"] = {
                "approve_marketplace": approve_marketplace_tx,
                "create_listing": create_listing_tx,
                "fund_buyer_usdc": funding_tx,
                "approve_usdc": approve_usdc_tx,
                "buy_listing": buy_listing_tx,
            }
            report["api_snapshots"]["after_buy"] = {
                "machines": client.get("/api/v1/machines").json(),
                "marketplace_listings": listings_after_buy,
                "machine_detail": machine_after_buy,
            }
            report["final_checks"] = {
                "listing_became_active": active_listing["state"] == "active",
                "buyer_is_canonical_owner": machine_after_buy["owner_user_id"] == buyer.user_id,
                "buyer_wallet_projected": machine_after_buy["owner_chain_address"] == buyer.address.lower(),
                "active_listing_cleared": machine_after_buy["active_listing"] is None,
                "marketplace_list_empty_for_machine": not any(
                    item["machine_id"] == machine_id for item in listings_after_buy
                ),
            }
    finally:
        DEFAULT_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if anvil_process is not None:
            anvil_process.terminate()
            try:
                anvil_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                anvil_process.kill()

    print(json.dumps(report["final_checks"], ensure_ascii=False, indent=2))
    print(f"report={DEFAULT_REPORT_PATH}")


if __name__ == "__main__":
    main()
