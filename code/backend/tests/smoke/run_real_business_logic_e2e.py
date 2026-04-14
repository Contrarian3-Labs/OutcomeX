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

import requests
from eth_account import Account
from fastapi.testclient import TestClient
from web3 import HTTPProvider, Web3

Account.enable_unaudited_hdwallet_features()

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "code" / "backend"
CONTRACTS_ROOT = REPO_ROOT / "code" / "contracts"
BROADCAST_RUN_PATH = CONTRACTS_ROOT / "broadcast" / "DeployLocal.s.sol" / "133" / "runWithConfig-latest.json"
DEFAULT_RPC_URL = os.getenv("OUTCOMEX_E2E_RPC_URL", "http://127.0.0.1:8545")
DEFAULT_CHAIN_ID = int(os.getenv("OUTCOMEX_E2E_CHAIN_ID", "133"))
DEFAULT_MNEMONIC = os.getenv(
    "OUTCOMEX_E2E_MNEMONIC",
    "test test test test test test test test test test test junk",
)
DEFAULT_DB_PATH = Path(os.getenv("OUTCOMEX_E2E_DB_PATH", "/tmp/outcomex-business-e2e.sqlite"))
DEFAULT_OUTPUT_ROOT = Path(os.getenv("OUTCOMEX_E2E_OUTPUT_ROOT", "/tmp/outcomex-business-e2e-output"))
DEFAULT_REPORT_PATH = Path(os.getenv("OUTCOMEX_E2E_REPORT_PATH", "/tmp/outcomex-business-e2e-report.json"))

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
        "name": "transferFrom",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [],
    }
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
    revenue_vault: str
    settlement_controller: str
    order_book: str
    order_payment_router: str
    sample_machine_id: int


class _ManualIndexerStatus:
    enabled = False
    reason = "manual_poll_in_e2e"


class _ManualIndexerProxy:
    status = _ManualIndexerStatus()

    def poll_once(self) -> None:
        return None

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        settlement_id
        return None


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
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
            chain_id = _rpc_call(rpc_url, "eth_chainId", [])
            if chain_id is not None:
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

    port = Web3(HTTPProvider()).provider.endpoint_uri  # pragma: no cover - defensive only
    port = port
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
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_rpc(rpc_url)
    except Exception:
        if process.stdout is not None:
            output = process.stdout.read()
        else:
            output = ""
        process.terminate()
        raise RuntimeError(f"anvil_start_failed:{output}")
    return process


def _deploy_contracts(*, rpc_url: str, admin: LocalAccount, treasury: LocalAccount, machine_owner: LocalAccount) -> Deployment:
    env = os.environ.copy()
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
    subprocess.run(command, cwd=CONTRACTS_ROOT, check=True, env=env, capture_output=True, text=True)
    payload = json.loads(BROADCAST_RUN_PATH.read_text(encoding="utf-8"))
    addresses: dict[str, str] = {}
    for tx in payload.get("transactions", []):
        if tx.get("transactionType") == "CREATE":
            contract_name = str(tx.get("contractName"))
            contract_address = tx.get("contractAddress")
            if contract_name and contract_address:
                addresses[contract_name] = Web3.to_checksum_address(contract_address)
    if not all(
        name in addresses
        for name in (
            "MockUSDCWithAuthorization",
            "MockUSDT",
            "PWRToken",
            "MachineAssetNFT",
            "RevenueVault",
            "SettlementController",
            "OrderBook",
            "OrderPaymentRouter",
        )
    ):
        raise RuntimeError("deployment_addresses_incomplete")
    raw_return = str(payload.get("returns", {}).get("deployed", {}).get("value", ""))
    pieces = [part.strip() for part in raw_return.strip("() ").split(",") if part.strip()]
    if len(pieces) < 9:
        raise RuntimeError(f"deployment_return_unexpected:{raw_return}")
    return Deployment(
        usdc=addresses["MockUSDCWithAuthorization"],
        usdt=addresses["MockUSDT"],
        pwr=addresses["PWRToken"],
        machine_asset=addresses["MachineAssetNFT"],
        revenue_vault=addresses["RevenueVault"],
        settlement_controller=addresses["SettlementController"],
        order_book=addresses["OrderBook"],
        order_payment_router=addresses["OrderPaymentRouter"],
        sample_machine_id=int(pieces[-1]),
    )


def _configure_backend_env(*, deployment: Deployment, admin: LocalAccount, buyer: LocalAccount, owner: LocalAccount, treasury: LocalAccount, transferee: LocalAccount) -> None:
    buyer_wallet_map = {
        buyer.user_id: buyer.address,
        owner.user_id: owner.address,
        treasury.user_id: treasury.address,
        transferee.user_id: transferee.address,
    }
    user_signers = {
        buyer.user_id: buyer.private_key,
        owner.user_id: owner.private_key,
        transferee.user_id: transferee.private_key,
    }
    os.environ.update(
        {
            "OUTCOMEX_ENV": "dev",
            "OUTCOMEX_DATABASE_URL": f"sqlite+pysqlite:///{DEFAULT_DB_PATH.as_posix()}",
            "OUTCOMEX_AUTO_CREATE_TABLES": "true",
            "OUTCOMEX_ONCHAIN_RPC_URL": DEFAULT_RPC_URL,
            "OUTCOMEX_ONCHAIN_CHAIN_ID": str(DEFAULT_CHAIN_ID),
            "OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS": deployment.machine_asset,
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
            "OUTCOMEX_USER_SIGNER_PRIVATE_KEYS_JSON": json.dumps(user_signers),
            "OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT": str(DEFAULT_OUTPUT_ROOT),
            "OUTCOMEX_ONCHAIN_INDEXER_ENABLED": "true",
            "OUTCOMEX_ONCHAIN_INDEXER_POLL_SECONDS": "0.2",
            "OUTCOMEX_ONCHAIN_INDEXER_CONFIRMATION_DEPTH": "0",
            "OUTCOMEX_EXECUTION_SYNC_ENABLED": "false",
        "OUTCOMEX_HSP_APP_KEY": "ak_test",
        "OUTCOMEX_HSP_APP_SECRET": "dev-key",
        "OUTCOMEX_HSP_PAY_TO_ADDRESS": treasury.address,
        "OUTCOMEX_HSP_REDIRECT_URL": "https://outcomex.local/mock-hsp",
        "OUTCOMEX_HSP_SUPPORTED_CURRENCIES": "USDC,USDT",
        "OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM": "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIEf8gQYenT5tskecihwTBGvrfqSTA3hRrunNTOADm/jJoAcGBSuBBAAK\noUQDQgAEOas7ZFkne5CsJx2VH70raQ4h9vSAmPe3Gtw2WKoz4yicVfBrPcc2LQHt\nBKXyZPxdDRrU0XLRNQJZxluyoE0Vaw==\n-----END EC PRIVATE KEY-----",
        }
    )


def _import_backend_modules() -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.core.config import reset_settings_cache
    from app.core.container import get_container, reset_container_cache
    from app.main import create_app
    from app.onchain.lifecycle_service import reset_onchain_lifecycle_service_cache
    from app.domain.models import Machine, Order
    from app.integrations.hsp_adapter import HSPMerchantOrder
    from app.onchain.order_writer import OrderWriter
    from app.onchain.tx_sender import encode_contract_call

    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()
    return {
        "create_app": create_app,
        "get_container": get_container,
        "Machine": Machine,
        "Order": Order,
        "OrderWriter": OrderWriter,
        "encode_contract_call": encode_contract_call,
        "HSPMerchantOrder": HSPMerchantOrder,
    }


def _web3() -> Web3:
    provider = HTTPProvider(DEFAULT_RPC_URL, request_kwargs={"timeout": 30})
    web3 = Web3(provider)
    if not web3.is_connected():
        raise RuntimeError("web3_not_connected")
    return web3


def _send_transaction(*, web3: Web3, private_key: str, to: str, data: str, allow_revert: bool = False) -> dict[str, Any]:
    account = Account.from_key(private_key)
    tx = {
        "chainId": DEFAULT_CHAIN_ID,
        "from": account.address,
        "to": Web3.to_checksum_address(to),
        "data": data,
        "nonce": web3.eth.get_transaction_count(account.address, "pending"),
        "gasPrice": web3.eth.gas_price,
        "value": 0,
    }
    if allow_revert:
        tx["gas"] = 500_000
    else:
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


def _smallest_units_from_cents(amount_cents: int) -> str:
    return str(amount_cents * 10_000)


def _approve_erc20_max(*, web3: Web3, token_address: str, owner_private_key: str, spender: str) -> dict[str, Any]:
    calldata = _contract_calldata(
        web3=web3,
        contract_address=token_address,
        abi=ERC20_ABI,
        fn_name="approve",
        args=[spender, (2**256) - 1],
    )
    return _send_transaction(
        web3=web3,
        private_key=owner_private_key,
        to=token_address,
        data=calldata,
    )


def _order_writer_call(modules: dict[str, Any], *, container, order_id: str, action: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    with container.session_factory() as db:
        order = db.get(modules["Order"], order_id)
        if order is None:
            raise RuntimeError(f"order_not_found:{order_id}")
        writer = modules["OrderWriter"]()
        if action == "confirm":
            write_result = writer.confirm_result(order)
        elif action == "reject":
            write_result = writer.reject_valid_preview(order)
        elif action == "claim_refund":
            write_result = writer.claim_refund(currency=kwargs["currency"], user_id=kwargs["user_id"], order_id=order.id)
        else:
            raise RuntimeError(f"unsupported_order_action:{action}")
        calldata = modules["encode_contract_call"](write_result)
        if calldata is None:
            raise RuntimeError(f"calldata_missing:{action}")
        return write_result.contract_address, write_result.payload | {"calldata": calldata}


def _machine_writer_call(modules: dict[str, Any], *, container, machine_id: str, action: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    with container.session_factory() as db:
        machine = db.get(modules["Machine"], machine_id)
        if machine is None:
            raise RuntimeError(f"machine_not_found:{machine_id}")
        writer = modules["OrderWriter"]()
        if action == "claim_machine_revenue":
            write_result = writer.claim_machine_revenue(machine)
        else:
            raise RuntimeError(f"unsupported_machine_action:{action}")
        calldata = modules["encode_contract_call"](write_result)
        if calldata is None:
            raise RuntimeError(f"calldata_missing:{action}")
        return write_result.contract_address, write_result.payload | {"calldata": calldata}


def _platform_claim_call(modules: dict[str, Any], *, currency: str) -> tuple[str, dict[str, Any]]:
    writer = modules["OrderWriter"]()
    write_result = writer.claim_platform_revenue(currency=currency)
    calldata = modules["encode_contract_call"](write_result)
    if calldata is None:
        raise RuntimeError("calldata_missing:platform_claim")
    return write_result.contract_address, write_result.payload | {"calldata": calldata}


def _build_hsp_webhook(*, client: TestClient, payment_intent: dict[str, Any], amount_cents: int, tx_signature: str) -> dict[str, Any]:
    from app.core.container import get_container

    body = {
        "request_id": f"evt-{payment_intent['payment_id']}",
        "payment_request_id": payment_intent["provider_reference"],
        "cart_mandate_id": payment_intent["merchant_order_id"],
        "payment_url": payment_intent["checkout_url"],
        "status": "completed",
        "amount": _smallest_units_from_cents(amount_cents),
        "token": "USDC",
        "tx_signature": tx_signature,
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = get_container().hsp_adapter.build_webhook_signature(body=raw, timestamp=timestamp)
    response = client.post(
        "/api/v1/payments/hsp/webhooks",
        content=raw,
        headers={
            "content-type": "application/json",
            "x-signature": f"t={timestamp},v1={signature}",
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    _ensure_fresh_paths()
    admin = _derive_account(0, user_id="admin-1")
    owner = _derive_account(1, user_id="owner-1")
    buyer = _derive_account(2, user_id="buyer-1")
    treasury = _derive_account(3, user_id="treasury-1")
    transferee = _derive_account(4, user_id="transferee-1")

    anvil_process = _start_anvil_if_needed(DEFAULT_RPC_URL)
    web3 = _web3()

    report: dict[str, Any] = {
        "rpc_url": DEFAULT_RPC_URL,
        "chain_id": DEFAULT_CHAIN_ID,
        "anvil_started_by_script": anvil_process is not None,
        "accounts": {
            "admin": admin.address,
            "owner": owner.address,
            "buyer": buyer.address,
            "treasury": treasury.address,
            "transferee": transferee.address,
        },
        "scenarios": {},
    }

    try:
        deployment = _deploy_contracts(rpc_url=DEFAULT_RPC_URL, admin=admin, treasury=treasury, machine_owner=owner)
        report["deployment"] = deployment.__dict__
        report["router_allowances"] = {
            "usdc": _approve_erc20_max(
                web3=web3,
                token_address=deployment.usdc,
                owner_private_key=admin.private_key,
                spender=deployment.order_payment_router,
            ),
            "usdt": _approve_erc20_max(
                web3=web3,
                token_address=deployment.usdt,
                owner_private_key=admin.private_key,
                spender=deployment.order_payment_router,
            ),
        }
        _configure_backend_env(
            deployment=deployment,
            admin=admin,
            buyer=buyer,
            owner=owner,
            treasury=treasury,
            transferee=transferee,
        )
        modules = _import_backend_modules()
        container = modules["get_container"]()
        real_indexer = container.onchain_indexer
        container.onchain_indexer = _ManualIndexerProxy()
        app = modules["create_app"]()
        container.hsp_adapter.create_payment_intent = lambda order_id, amount_cents, currency, expires_at: modules["HSPMerchantOrder"](
            provider="hsp",
            merchant_order_id=f"merchant-{order_id}",
            flow_id=f"flow-{order_id}",
            provider_reference=f"PAY-REQ-{order_id}",
            payment_url=f"https://outcomex.local/mock-hsp/{order_id}",
            amount_cents=amount_cents,
            currency=currency.upper(),
            provider_payload={"mode": "mock-e2e", "expires_at": expires_at.isoformat() if expires_at else None},
        )
        with TestClient(app) as client:
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

            client.post("/api/v1/debug/smoke-reset").raise_for_status()

            machine = client.post(
                "/api/v1/machines",
                json={
                    "owner_user_id": owner.user_id,
                    "display_name": "OutcomeX Hosted Machine A",
                },
            ).json()
            machine_id = machine["id"]
            sample_machine = _wait_until(
                "machine owner projection",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id and item["owner_chain_address"] == owner.address.lower()
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            report["machine_created"] = sample_machine

            plan_hsp = client.post(
                "/api/v1/chat/plans",
                json={
                    "user_id": buyer.user_id,
                    "chat_session_id": "chat-hsp",
                    "user_message": "Build a launch page with clean copy, a hero image, and final delivery assets.",
                    "mode": "quality",
                    "input_files": ["brief.md"],
                },
            )
            plan_hsp.raise_for_status()
            plan_hsp_payload = plan_hsp.json()
            selected_hsp_plan = plan_hsp_payload["recommended_plans"][0]
            order_hsp_resp = client.post(
                "/api/v1/orders",
                json={
                    "user_id": buyer.user_id,
                    "machine_id": machine_id,
                    "chat_session_id": "chat-hsp",
                    "user_prompt": plan_hsp_payload["user_message"],
                    "quoted_amount_cents": 1000,
                    "input_files": ["brief.md"],
                    "execution_strategy": "quality",
                    "selected_plan_id": selected_hsp_plan["plan_id"],
                },
            )
            order_hsp_resp.raise_for_status()
            order_hsp = order_hsp_resp.json()
            payment_intent = client.post(
                f"/api/v1/payments/orders/{order_hsp['id']}/intent",
                json={"amount_cents": 1000, "currency": "USDC"},
            )
            payment_intent.raise_for_status()
            payment_intent_payload = payment_intent.json()
            webhook_result = _build_hsp_webhook(
                client=client,
                payment_intent=payment_intent_payload,
                amount_cents=1000,
                tx_signature="0xhsp000000000000000000000000000000000000000000000000000000000001",
            )
            paid_order_hsp = _wait_until(
                "hsp paid projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_hsp['id']}").json()
                    if client.get(f"/api/v1/orders/{order_hsp['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PAID"
                    else None
                ),
                poller=poll_indexer,
            )
            mock_ready_hsp = client.post(f"/api/v1/orders/{order_hsp['id']}/mock-result-ready", json={"valid_preview": True})
            mock_ready_hsp.raise_for_status()
            preview_ready_hsp = _wait_until(
                "preview ready projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_hsp['id']}").json()
                    if client.get(f"/api/v1/orders/{order_hsp['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PREVIEW_READY"
                    else None
                ),
                poller=poll_indexer,
            )
            confirm_address, confirm_payload = _order_writer_call(
                modules,
                container=container,
                order_id=order_hsp["id"],
                action="confirm",
            )
            confirm_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=confirm_address,
                data=confirm_payload["calldata"],
            )
            confirmed_order_hsp = _wait_until(
                "confirmed settlement projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_hsp['id']}").json()
                    if client.get(f"/api/v1/orders/{order_hsp['id']}").json()["state"] == "result_confirmed"
                    else None
                ),
                poller=poll_indexer,
            )
            blocked_transfer_data = _contract_calldata(
                web3=web3,
                contract_address=deployment.machine_asset,
                abi=ERC721_ABI,
                fn_name="transferFrom",
                args=[owner.address, transferee.address, int(confirmed_order_hsp["onchain_machine_id"])],
            )
            blocked_transfer_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=deployment.machine_asset,
                data=blocked_transfer_data,
                allow_revert=True,
            )
            claim_machine_address, claim_machine_payload = _machine_writer_call(
                modules,
                container=container,
                machine_id=machine_id,
                action="claim_machine_revenue",
            )
            machine_claim_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=claim_machine_address,
                data=claim_machine_payload["calldata"],
            )
            machine_after_claim = _wait_until(
                "machine unlock after machine claim",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id and item["transfer_ready"] is True and item["claimable_cents"] == 0
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            platform_claim_address, platform_claim_payload = _platform_claim_call(modules, currency="USDC")
            platform_claim_tx = _send_transaction(
                web3=web3,
                private_key=treasury.private_key,
                to=platform_claim_address,
                data=platform_claim_payload["calldata"],
            )
            platform_overview_usdc = _wait_until(
                "platform claim projection",
                lambda: (
                    client.get("/api/v1/revenue/platform/overview", params={"currency": "USDC"}).json()
                    if client.get("/api/v1/revenue/platform/overview", params={"currency": "USDC"}).json()["claimed_cents"]
                    > 0
                    else None
                ),
                poller=poll_indexer,
            )
            transfer_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=deployment.machine_asset,
                data=blocked_transfer_data,
            )
            transferred_machine = _wait_until(
                "machine ownership projection after transfer",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id and item["owner_user_id"] == transferee.user_id
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            owner_overview_after_hsp = client.get(f"/api/v1/revenue/accounts/{owner.user_id}/overview").json()
            report["scenarios"]["hsp_confirm_claim_transfer"] = {
                "plan": selected_hsp_plan,
                "order_id": order_hsp["id"],
                "payment_intent": payment_intent_payload,
                "webhook": webhook_result,
                "paid_order": paid_order_hsp,
                "preview_ready_order": preview_ready_hsp,
                "confirm_tx": confirm_tx,
                "blocked_transfer_tx": blocked_transfer_tx,
                "machine_claim_tx": machine_claim_tx,
                "platform_claim_tx": platform_claim_tx,
                "transfer_tx": transfer_tx,
                "confirmed_order": confirmed_order_hsp,
                "machine_after_claim": machine_after_claim,
                "platform_overview_usdc": platform_overview_usdc,
                "transferred_machine": transferred_machine,
                "owner_overview_after_hsp": owner_overview_after_hsp,
            }

            plan_pwr = client.post(
                "/api/v1/chat/plans",
                json={
                    "user_id": buyer.user_id,
                    "chat_session_id": "chat-pwr",
                    "user_message": "Generate a set of social promos and export final deliverables quickly.",
                    "mode": "efficiency",
                    "input_files": ["promo-brief.md"],
                },
            )
            plan_pwr.raise_for_status()
            plan_pwr_payload = plan_pwr.json()
            selected_pwr_plan = plan_pwr_payload["recommended_plans"][0]
            order_pwr_resp = client.post(
                "/api/v1/orders",
                json={
                    "user_id": buyer.user_id,
                    "machine_id": machine_id,
                    "chat_session_id": "chat-pwr",
                    "user_prompt": plan_pwr_payload["user_message"],
                    "quoted_amount_cents": 1000,
                    "input_files": ["promo-brief.md"],
                    "execution_strategy": "efficiency",
                    "selected_plan_id": selected_pwr_plan["plan_id"],
                },
            )
            order_pwr_resp.raise_for_status()
            order_pwr = order_pwr_resp.json()
            pwr_intent_resp = client.post(
                f"/api/v1/payments/orders/{order_pwr['id']}/direct-intent",
                json={
                    "amount_cents": 1000,
                    "currency": "PWR",
                    "wallet_address": buyer.address,
                },
            )
            pwr_intent_resp.raise_for_status()
            pwr_intent = pwr_intent_resp.json()
            pwr_amount = int(pwr_intent["submit_payload"]["pwr_amount"])
            pwr_transfer_data = _contract_calldata(
                web3=web3,
                contract_address=deployment.pwr,
                abi=ERC20_ABI,
                fn_name="transfer",
                args=[buyer.address, pwr_amount],
            )
            admin_pwr_transfer_tx = _send_transaction(
                web3=web3,
                private_key=admin.private_key,
                to=deployment.pwr,
                data=pwr_transfer_data,
            )
            approve_pwr_data = _contract_calldata(
                web3=web3,
                contract_address=deployment.pwr,
                abi=ERC20_ABI,
                fn_name="approve",
                args=[deployment.order_payment_router, pwr_amount],
            )
            buyer_pwr_approve_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=deployment.pwr,
                data=approve_pwr_data,
            )
            pwr_payment_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=pwr_intent["contract_address"],
                data=pwr_intent["calldata"],
            )
            sync_resp = client.post(
                f"/api/v1/payments/{pwr_intent['payment_id']}/sync-onchain",
                json={
                    "state": "pending",
                    "tx_hash": pwr_payment_tx["tx_hash"],
                    "wallet_address": buyer.address,
                },
            )
            sync_resp.raise_for_status()
            paid_order_pwr = _wait_until(
                "pwr paid projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_pwr['id']}").json()
                    if client.get(f"/api/v1/orders/{order_pwr['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PAID"
                    else None
                ),
                poller=poll_indexer,
            )
            mock_ready_pwr = client.post(f"/api/v1/orders/{order_pwr['id']}/mock-result-ready", json={"valid_preview": True})
            mock_ready_pwr.raise_for_status()
            _wait_until(
                "pwr preview ready projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_pwr['id']}").json()
                    if client.get(f"/api/v1/orders/{order_pwr['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PREVIEW_READY"
                    else None
                ),
                poller=poll_indexer,
            )
            reject_address, reject_payload = _order_writer_call(
                modules,
                container=container,
                order_id=order_pwr["id"],
                action="reject",
            )
            reject_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=reject_address,
                data=reject_payload["calldata"],
            )
            rejected_order = _wait_until(
                "rejected settlement projection",
                lambda: (
                    client.get(f"/api/v1/orders/{order_pwr['id']}").json()
                    if client.get(f"/api/v1/orders/{order_pwr['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "REJECTED"
                    else None
                ),
                poller=poll_indexer,
            )
            refund_actions = client.get(f"/api/v1/orders/{order_pwr['id']}/available-actions").json()
            claim_refund_address, claim_refund_payload = _order_writer_call(
                modules,
                container=container,
                order_id=order_pwr["id"],
                action="claim_refund",
                currency="PWR",
                user_id=buyer.user_id,
            )
            refund_claim_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=claim_refund_address,
                data=claim_refund_payload["calldata"],
            )
            machine_claim_address_2, machine_claim_payload_2 = _machine_writer_call(
                modules,
                container=container,
                machine_id=machine_id,
                action="claim_machine_revenue",
            )
            machine_claim_tx_2 = _send_transaction(
                web3=web3,
                private_key=transferee.private_key,
                to=machine_claim_address_2,
                data=machine_claim_payload_2["calldata"],
            )
            platform_claim_address_2, platform_claim_payload_2 = _platform_claim_call(modules, currency="PWR")
            platform_claim_tx_2 = _send_transaction(
                web3=web3,
                private_key=treasury.private_key,
                to=platform_claim_address_2,
                data=platform_claim_payload_2["calldata"],
            )
            buyer_claim_history = _wait_until(
                "buyer claim history after refund",
                lambda: (
                    client.get(f"/api/v1/revenue/accounts/{buyer.user_id}/claims").json()
                    if any(item["claim_kind"] == "refund" and item["currency"] == "PWR" for item in client.get(f"/api/v1/revenue/accounts/{buyer.user_id}/claims").json())
                    else None
                ),
                poller=poll_indexer,
            )
            machine_after_pwr_claim = _wait_until(
                "machine unlock after pwr claim",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == machine_id and item["transfer_ready"] is True
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            platform_overview_pwr = _wait_until(
                "platform pwr claim projection",
                lambda: (
                    client.get("/api/v1/revenue/platform/overview", params={"currency": "PWR"}).json()
                    if client.get("/api/v1/revenue/platform/overview", params={"currency": "PWR"}).json()["claimed_cents"] > 0
                    else None
                ),
                poller=poll_indexer,
            )
            report["scenarios"]["pwr_reject_refund_claim"] = {
                "plan": selected_pwr_plan,
                "order_id": order_pwr["id"],
                "pwr_intent": pwr_intent,
                "admin_pwr_transfer_tx": admin_pwr_transfer_tx,
                "buyer_pwr_approve_tx": buyer_pwr_approve_tx,
                "pwr_payment_tx": pwr_payment_tx,
                "sync_response": sync_resp.json(),
                "paid_order": paid_order_pwr,
                "reject_tx": reject_tx,
                "rejected_order": rejected_order,
                "refund_actions": refund_actions,
                "refund_claim_tx": refund_claim_tx,
                "machine_claim_tx": machine_claim_tx_2,
                "platform_claim_tx": platform_claim_tx_2,
                "buyer_claim_history": buyer_claim_history,
                "machine_after_claim": machine_after_pwr_claim,
                "platform_overview_pwr": platform_overview_pwr,
            }

            report["final_checks"] = {
                "hsp_transfer_blocked_before_claim": blocked_transfer_tx["status"] == 0,
                "machine_transferred_after_hsp_claim": transferred_machine["owner_user_id"] == transferee.user_id,
                "pwr_refund_claim_available": refund_actions["can_claim_refund"] is True,
                "platform_usdc_claimed": platform_overview_usdc["claimed_cents"] > 0,
                "platform_pwr_claimed": platform_overview_pwr["claimed_cents"] > 0,
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
