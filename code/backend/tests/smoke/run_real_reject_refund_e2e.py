import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests
from eth_account import Account
from web3 import Web3

try:
    from tests.smoke.chain_reset import json_rpc_call, prepare_clean_run_state, resolve_smoke_onchain_addresses, smoke_preflight_cleanup_paths
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.smoke.chain_reset import json_rpc_call, prepare_clean_run_state, resolve_smoke_onchain_addresses, smoke_preflight_cleanup_paths

API = os.getenv("OUTCOMEX_SMOKE_API", "http://127.0.0.1:8012/api/v1")
RPC = os.getenv("OUTCOMEX_ONCHAIN_RPC_URL", "http://127.0.0.1:8545")
BUYER_PK = os.getenv(
    "OUTCOMEX_ONCHAIN_BUYER_PRIVATE_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
)
OWNER_PK = os.getenv(
    "OUTCOMEX_ONCHAIN_MACHINE_OWNER_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
)
BUYER = Account.from_key(BUYER_PK).address
OWNER = Account.from_key(OWNER_PK).address
ADDR = resolve_smoke_onchain_addresses()
REPORT_PATH = Path("/tmp/outcomex-real-reject-refund-report.json")
SNAPSHOT_PATH = Path("/tmp/outcomex-anvil-smoke-snapshot.txt")

w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.is_connected(), "web3_not_connected"
CHAIN_ID = w3.eth.chain_id
pwr = w3.eth.contract(address=ADDR["pwr"], abi=[
    {"name": "approve", "type": "function", "stateMutability": "nonpayable", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}]},
])
router = w3.eth.contract(address=ADDR["router"], abi=[
    {"name": "createOrderAndPayWithPWR", "type": "function", "stateMutability": "nonpayable", "inputs": [{"name": "machineId", "type": "uint256"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "orderId", "type": "uint256"}]},
])
orderbook = w3.eth.contract(address=ADDR["orderbook"], abi=[
    {"name": "getOrder", "type": "function", "stateMutability": "view", "inputs": [{"name": "orderId", "type": "uint256"}], "outputs": [{"components": [{"name": "id", "type": "uint256"}, {"name": "machineId", "type": "uint256"}, {"name": "buyer", "type": "address"}, {"name": "grossAmount", "type": "uint256"}, {"name": "status", "type": "uint8"}, {"name": "previewValid", "type": "bool"}, {"name": "createdAt", "type": "uint64"}, {"name": "paidAt", "type": "uint64"}, {"name": "previewReadyAt", "type": "uint64"}, {"name": "settledAt", "type": "uint64"}], "type": "tuple"}]},
    {"name": "canTransfer", "type": "function", "stateMutability": "view", "inputs": [{"name": "machineId", "type": "uint256"}, {"name": "from", "type": "address"}, {"name": "to", "type": "address"}], "outputs": [{"name": "", "type": "bool"}, {"name": "", "type": "bytes32"}]},
])
settlement = w3.eth.contract(address=ADDR["settlement"], abi=[
    {"name": "refundableByToken", "type": "function", "stateMutability": "view", "inputs": [{"name": "buyer", "type": "address"}, {"name": "token", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "platformAccruedByToken", "type": "function", "stateMutability": "view", "inputs": [{"name": "token", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
])
revenue = w3.eth.contract(address=ADDR["revenue"], abi=[
    {"name": "claimableByMachineOwner", "type": "function", "stateMutability": "view", "inputs": [{"name": "machineId", "type": "uint256"}, {"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
])

sess = requests.Session()
report = {"scenarios": []}


def persist() -> None:
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def preflight_reset() -> str:
    snapshot_id = prepare_clean_run_state(
        snapshot_file=SNAPSHOT_PATH,
        rpc_call=lambda method, params: json_rpc_call(rpc_url=RPC, method=method, params=list(params)),
        cleanup_paths=smoke_preflight_cleanup_paths(report_path=REPORT_PATH),
    )
    reset_response = requests.post(f"{API}/debug/smoke-reset", timeout=180)
    reset_response.raise_for_status()
    report["backend_reset"] = reset_response.json()
    return snapshot_id


def api(method: str, path: str, **kwargs):
    resp = sess.request(method, f"{API}{path}", timeout=180, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = resp.text
    if resp.status_code >= 400:
        raise RuntimeError(f"api_error {method} {path} {resp.status_code}: {data}")
    return data


def sign_and_send(tx, private_key: str):
    account = Account.from_key(private_key)
    tx = dict(tx)
    tx.pop("maxFeePerGas", None)
    tx.pop("maxPriorityFeePerGas", None)
    tx["chainId"] = CHAIN_ID
    tx["nonce"] = w3.eth.get_transaction_count(account.address)
    tx["gasPrice"] = w3.eth.gas_price
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"tx_failed:{tx_hash.hex()}")
    return "0x" + tx_hash.hex(), receipt


def ensure_machine():
    machines = api("GET", "/machines")
    machine = next((item for item in machines if item.get("onchain_machine_id") == "1"), None)
    if machine is None:
        machine = api("POST", "/machines", json={
            "display_name": "Local E2E Machine",
            "owner_user_id": "owner-1",
            "onchain_machine_id": "1",
        })
    return machine


def fetch_recommended_plans(*, prompt: str, user_id: str = "user-1"):
    payload = api("POST", "/chat/plans", json={
        "user_id": user_id,
        "chat_session_id": f"chat-plan-{uuid.uuid4().hex[:12]}",
        "user_message": prompt,
    })
    recommended = payload["recommended_plans"]
    assert len(recommended) == 3, recommended
    assert [plan["native_plan_index"] for plan in recommended] == [0, 1, 2], recommended
    assert [plan["strategy"] for plan in recommended] == ["quality", "efficiency", "simplicity"], recommended
    return payload


def create_direct_paid_order(*, machine_id: str, prompt: str, execution_strategy: str = "simplicity"):
    order = api("POST", "/orders", json={
        "user_id": "user-1",
        "machine_id": machine_id,
        "chat_session_id": f"chat-{uuid.uuid4().hex[:12]}",
        "user_prompt": prompt,
        "quoted_amount_cents": 1000,
        "execution_strategy": execution_strategy,
    })
    intent = api("POST", f"/payments/orders/{order['id']}/direct-intent", json={"amount_cents": 1000, "currency": "PWR"})
    pwr_amount = int(intent["submit_payload"]["pwr_amount"])
    approve_tx = pwr.functions.approve(ADDR["router"], pwr_amount).build_transaction({"from": BUYER})
    approve_hash, _ = sign_and_send(approve_tx, BUYER_PK)
    pay_tx = router.functions.createOrderAndPayWithPWR(int(order["onchain_machine_id"] or 1), pwr_amount).build_transaction({"from": BUYER})
    pay_hash, _ = sign_and_send(pay_tx, BUYER_PK)
    sync = api("POST", f"/payments/{intent['payment_id']}/sync-onchain", json={"state": "succeeded", "tx_hash": pay_hash, "wallet_address": BUYER})
    order_after_pay = api("GET", f"/orders/{order['id']}")
    return {
        "order": order_after_pay,
        "payment_intent": intent,
        "pwr_amount": pwr_amount,
        "approve_tx_hash": approve_hash,
        "pay_tx_hash": pay_hash,
        "sync": sync,
    }


def chain_balances(machine_chain_id: int):
    refundable = settlement.functions.refundableByToken(BUYER, ADDR["pwr"]).call()
    platform = settlement.functions.platformAccruedByToken(ADDR["pwr"]).call()
    claimable = revenue.functions.claimableByMachineOwner(machine_chain_id, OWNER).call()
    transfer = orderbook.functions.canTransfer(machine_chain_id, OWNER, BUYER).call()
    return {
        "refundable": int(refundable),
        "platform": int(platform),
        "claimable": int(claimable),
        "transfer_allowed": bool(transfer[0]),
        "transfer_reason": transfer[1].hex(),
    }


def expect_delta(before: dict, after: dict, *, refund: int, platform: int, claimable: int):
    assert after["refundable"] - before["refundable"] == refund, (before, after, refund)
    assert after["platform"] - before["platform"] == platform, (before, after, platform)
    assert after["claimable"] - before["claimable"] == claimable, (before, after, claimable)


def poll_execution(run_id: str):
    terminal = None
    for i in range(210):
        snap = api("GET", f"/execution-runs/{run_id}")
        if snap["status"] in {"succeeded", "failed", "cancelled"}:
            terminal = snap
            break
        time.sleep(2)
    if terminal is None:
        raise RuntimeError("execution_timeout")
    return terminal


def wait_for_preview_ready(order_id: str):
    for _ in range(120):
        current_order = api("GET", f"/orders/{order_id}")
        meta = current_order.get("execution_metadata") or {}
        if meta.get("onchain_preview_ready_tx_hash"):
            return current_order, meta
        time.sleep(1)
    raise RuntimeError("preview_ready_not_broadcasted")


def scenario_reject_valid_preview(machine: dict):
    scenario = {"name": "reject_valid_preview", "steps": []}
    report["scenarios"].append(scenario)
    persist()

    prompt = (
        "Create a markdown file named reject_preview_test.md with a title Reject Preview Test "
        "and exactly three bullet points: preview, rejection, settlement."
    )
    plans = fetch_recommended_plans(prompt=prompt)
    created = create_direct_paid_order(machine_id=machine["id"], prompt=prompt)
    order = created["order"]
    scenario["steps"].append({"step": "plans", "payload": {"count": len(plans["recommended_plans"]), "indices": [plan["native_plan_index"] for plan in plans["recommended_plans"]], "strategies": [plan["strategy"] for plan in plans["recommended_plans"]]}})
    scenario["steps"].append({"step": "paid", "payload": created})

    before = chain_balances(int(machine["onchain_machine_id"]))
    scenario["steps"].append({"step": "balances_before_reject", "payload": before})

    run = api("POST", f"/orders/{order['id']}/start-execution")
    terminal = poll_execution(run["id"])
    if terminal["status"] != "succeeded":
        raise RuntimeError(f"reject_execution_not_succeeded:{terminal.get('error')}")
    current_order, meta = wait_for_preview_ready(order["id"])
    onchain_order_id = int(current_order["onchain_order_id"])
    onchain_preview = orderbook.functions.getOrder(onchain_order_id).call()
    scenario["steps"].append({
        "step": "execution_ready",
        "payload": {
            "run_id": run["id"],
            "artifacts": terminal.get("artifact_manifest"),
            "models": terminal.get("model_usage_manifest"),
            "preview_tx_hash": meta.get("onchain_preview_ready_tx_hash"),
            "chain_status": int(onchain_preview[4]),
            "preview_valid": bool(onchain_preview[5]),
        },
    })

    reject = api("POST", f"/orders/{order['id']}/reject-valid-preview")
    after_reject_order = api("GET", f"/orders/{order['id']}")
    onchain_rejected = orderbook.functions.getOrder(onchain_order_id).call()
    after = chain_balances(int(machine["onchain_machine_id"]))
    expected_refund = created["pwr_amount"] * 7000 // 10000
    rejection_fee = created["pwr_amount"] - expected_refund
    expected_platform = rejection_fee * 1000 // 10000
    expected_machine = rejection_fee - expected_platform
    expect_delta(before, after, refund=expected_refund, platform=expected_platform, claimable=expected_machine)
    assert after_reject_order["state"] == "cancelled"
    assert after_reject_order["settlement_state"] == "distributed"
    assert int(onchain_rejected[4]) == 5
    scenario["steps"].append({
        "step": "rejected",
        "payload": {
            "reject_api": reject,
            "order_state": after_reject_order["state"],
            "settlement_state": after_reject_order["settlement_state"],
            "chain_status": int(onchain_rejected[4]),
            "refund_delta": after["refundable"] - before["refundable"],
            "platform_delta": after["platform"] - before["platform"],
            "machine_delta": after["claimable"] - before["claimable"],
            "transfer_allowed_after_reject": after["transfer_allowed"],
            "transfer_reason_after_reject": after["transfer_reason"],
        },
    })

    refund_claim = api("POST", f"/settlement/orders/{order['id']}/claim-refund")
    machine_claim = api("POST", f"/revenue/machines/{machine['id']}/claim")
    platform_claim = api("POST", "/settlement/platform/claim", json={"currency": "PWR"})
    after_claims = chain_balances(int(machine["onchain_machine_id"]))
    assert after_claims["refundable"] == 0
    assert after_claims["platform"] == 0
    assert after_claims["claimable"] == 0
    assert after_claims["transfer_allowed"] is True
    scenario["steps"].append({
        "step": "claims",
        "payload": {
            "refund_claim": refund_claim,
            "machine_claim": machine_claim,
            "platform_claim": platform_claim,
            "balances_after_claims": after_claims,
        },
    })
    persist()


def scenario_invalid_preview_full_refund(machine: dict):
    scenario = {"name": "invalid_preview_full_refund", "steps": []}
    report["scenarios"].append(scenario)
    persist()

    prompt = "Create a markdown file named invalid_preview_test.md with a title Invalid Preview Test."
    created = create_direct_paid_order(machine_id=machine["id"], prompt=prompt)
    order = created["order"]
    scenario["steps"].append({"step": "paid", "payload": created})

    before = chain_balances(int(machine["onchain_machine_id"]))
    ready = api("POST", f"/orders/{order['id']}/mock-result-ready", json={"valid_preview": False})
    order_after_ready = api("GET", f"/orders/{order['id']}")
    onchain_order_id = int(order_after_ready["onchain_order_id"])
    onchain_preview = orderbook.functions.getOrder(onchain_order_id).call()
    scenario["steps"].append({
        "step": "invalid_preview_marked",
        "payload": {
            "ready_api": ready,
            "preview_tx_hash": (order_after_ready.get("execution_metadata") or {}).get("onchain_preview_ready_tx_hash"),
            "chain_status": int(onchain_preview[4]),
            "preview_valid": bool(onchain_preview[5]),
        },
    })

    refund = api("POST", f"/orders/{order['id']}/refund-failed-or-no-valid-preview")
    refunded_order = api("GET", f"/orders/{order['id']}")
    onchain_refunded = orderbook.functions.getOrder(onchain_order_id).call()
    after = chain_balances(int(machine["onchain_machine_id"]))
    expect_delta(before, after, refund=created["pwr_amount"], platform=0, claimable=0)
    assert refunded_order["state"] == "cancelled"
    assert refunded_order["settlement_state"] == "distributed"
    assert int(onchain_refunded[4]) == 6
    assert after["transfer_allowed"] is True
    scenario["steps"].append({
        "step": "refunded",
        "payload": {
            "refund_api": refund,
            "order_state": refunded_order["state"],
            "settlement_state": refunded_order["settlement_state"],
            "chain_status": int(onchain_refunded[4]),
            "refund_delta": after["refundable"] - before["refundable"],
            "platform_delta": after["platform"] - before["platform"],
            "machine_delta": after["claimable"] - before["claimable"],
            "transfer_allowed_after_refund": after["transfer_allowed"],
            "transfer_reason_after_refund": after["transfer_reason"],
        },
    })

    refund_claim = api("POST", f"/settlement/orders/{order['id']}/claim-refund")
    after_claim = chain_balances(int(machine["onchain_machine_id"]))
    assert after_claim["refundable"] == 0
    scenario["steps"].append({
        "step": "refund_claimed",
        "payload": {
            "refund_claim": refund_claim,
            "balances_after_claim": after_claim,
        },
    })
    persist()


def main():
    snapshot_id = preflight_reset()
    report["snapshot_id"] = snapshot_id
    health = api("GET", "/health")
    report["health"] = health
    machine = ensure_machine()
    report["machine"] = machine
    persist()
    scenario_reject_valid_preview(machine)
    scenario_invalid_preview_full_refund(machine)
    report["final"] = "ok"
    persist()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
