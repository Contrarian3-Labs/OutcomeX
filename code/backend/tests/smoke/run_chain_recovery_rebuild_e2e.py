#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.smoke.run_marketplace_buy_e2e import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REPORT_PATH,
    ERC721_ABI,
    MARKETPLACE_ABI,
    _approve_erc20_max,
    _configure_backend_env,
    _contract_calldata,
    _deploy_contracts,
    _derive_account,
    _ensure_fresh_paths,
    _send_transaction,
    _start_anvil_if_needed,
    _wait_until,
    _web3,
)
from tests.smoke.run_real_business_logic_e2e import (
    _build_hsp_webhook,
    _import_backend_modules as _import_backend_modules,
    _machine_writer_call,
    _order_writer_call,
)


class _ManualIndexerStatus:
    enabled = False
    reason = "manual_poll_in_recovery_e2e"


class _ManualIndexerProxy:
    status = _ManualIndexerStatus()

    def poll_once(self) -> None:
        return None

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        settlement_id
        return None


def _snapshot_state(*, client: TestClient, buyer_user_id: str, owner_user_id: str) -> dict[str, Any]:
    return {
        "machines": client.get("/api/v1/machines").json(),
        "listings": client.get("/api/v1/marketplace/listings").json(),
        "orders": client.get("/api/v1/orders", params={"user_id": buyer_user_id}).json(),
        "owner_revenue": client.get(f"/api/v1/revenue/accounts/{owner_user_id}/overview").json(),
    }


def _db_counts(modules: dict[str, Any]) -> dict[str, int]:
    from sqlalchemy import func, select
    from app.domain.models import Machine, MachineListing, Order, Payment, RevenueEntry, SettlementClaimRecord, SettlementRecord

    container = modules["get_container"]()
    with container.session_factory() as db:
        return {
            "machines": int(db.scalar(select(func.count(Machine.id))) or 0),
            "listings": int(db.scalar(select(func.count(MachineListing.id))) or 0),
            "orders": int(db.scalar(select(func.count(Order.id))) or 0),
            "payments": int(db.scalar(select(func.count(Payment.id))) or 0),
            "settlements": int(db.scalar(select(func.count(SettlementRecord.id))) or 0),
            "revenue_entries": int(db.scalar(select(func.count(RevenueEntry.id))) or 0),
            "claim_records": int(db.scalar(select(func.count(SettlementClaimRecord.id))) or 0),
        }


def _payment_snapshot(modules: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from app.domain.models import Order, Payment

    container = modules["get_container"]()
    with container.session_factory() as db:
        payment = db.scalar(select(Payment).order_by(Payment.created_at.desc()))
        order = db.scalar(select(Order).order_by(Order.created_at.desc()))
        return {
            "payment": {
                "provider": payment.provider,
                "currency": payment.currency,
                "amount_cents": payment.amount_cents,
                "state": payment.state.value,
                "callback_tx_hash": payment.callback_tx_hash,
            }
            if payment is not None
            else None,
            "order": {
                "id": order.id,
                "onchain_order_id": order.onchain_order_id,
                "state": order.state.value,
                "settlement_state": order.settlement_state.value,
                "beneficiary_user_id": order.settlement_beneficiary_user_id,
            }
            if order is not None
            else None,
        }


def main() -> None:
    report: dict[str, Any] = {
        "scenario": "chain_recovery_rebuild_e2e",
        "report_path": str(DEFAULT_REPORT_PATH),
        "db_path": str(DEFAULT_DB_PATH),
        "output_root": str(DEFAULT_OUTPUT_ROOT),
        "snapshots": {},
        "transactions": {},
        "assertions": {},
    }

    anvil_process = None
    try:
        _ensure_fresh_paths()
        anvil_process = _start_anvil_if_needed(os.getenv("OUTCOMEX_MARKETPLACE_E2E_RPC_URL", "http://127.0.0.1:8545"))

        admin = _derive_account(0, user_id="admin")
        treasury = _derive_account(1, user_id="treasury")
        owner = _derive_account(2, user_id="owner-1")
        buyer = _derive_account(3, user_id="buyer-1")

        deployment = _deploy_contracts(rpc_url=os.getenv("OUTCOMEX_MARKETPLACE_E2E_RPC_URL", "http://127.0.0.1:8545"), admin=admin, treasury=treasury, machine_owner=owner)
        _configure_backend_env(
            deployment=deployment,
            admin=admin,
            buyer=buyer,
            owner=owner,
            treasury=treasury,
        )
        os.environ.update(
            {
                "OUTCOMEX_USER_SIGNER_PRIVATE_KEYS_JSON": json.dumps(
                    {
                        buyer.user_id: buyer.private_key,
                        owner.user_id: owner.private_key,
                    }
                ),
                "OUTCOMEX_HSP_APP_KEY": "ak_test",
                "OUTCOMEX_HSP_APP_SECRET": "dev-key",
                "OUTCOMEX_HSP_PAY_TO_ADDRESS": treasury.address,
                "OUTCOMEX_HSP_REDIRECT_URL": "https://outcomex.local/mock-hsp",
                "OUTCOMEX_HSP_SUPPORTED_CURRENCIES": "USDC,USDT",
                "OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM": "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIEf8gQYenT5tskecihwTBGvrfqSTA3hRrunNTOADm/jJoAcGBSuBBAAK\noUQDQgAEOas7ZFkne5CsJx2VH70raQ4h9vSAmPe3Gtw2WKoz4yicVfBrPcc2LQHt\nBKXyZPxdDRrU0XLRNQJZxluyoE0Vaw==\n-----END EC PRIVATE KEY-----",
            }
        )
        os.environ["OUTCOMEX_ONCHAIN_INDEXER_BOOTSTRAP_BLOCK"] = "0"

        modules = _import_backend_modules()
        app = modules["create_app"]()
        container = modules["get_container"]()
        container.hsp_adapter.create_payment_intent = lambda order_id, amount_cents, currency, expires_at: modules["HSPMerchantOrder"](
            provider="hsp",
            merchant_order_id=f"merchant-{order_id}",
            flow_id=f"flow-{order_id}",
            provider_reference=f"PAY-REQ-{order_id}",
            payment_url=f"https://outcomex.local/mock-hsp/{order_id}",
            amount_cents=amount_cents,
            currency=currency.upper(),
            provider_payload={"mode": "mock-recovery-e2e", "expires_at": expires_at.isoformat() if expires_at else None},
        )
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
                    }
                )

        with TestClient(app) as client:
            client.post("/api/v1/debug/smoke-reset").raise_for_status()

            listing_machine = client.post(
                "/api/v1/machines",
                json={"owner_user_id": owner.user_id, "display_name": "Recovery Listing Machine"},
            ).json()
            task_machine = client.post(
                "/api/v1/machines",
                json={"owner_user_id": owner.user_id, "display_name": "Recovery Task Machine"},
            ).json()

            listing_machine_projected = _wait_until(
                "listing machine projected",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == listing_machine["id"] and item["owner_chain_address"] == owner.address.lower()
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            task_machine_projected = _wait_until(
                "task machine projected",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/machines").json()
                        if item["id"] == task_machine["id"] and item["owner_chain_address"] == owner.address.lower()
                    ),
                    None,
                ),
                poller=poll_indexer,
            )

            report["snapshots"]["machines_projected"] = {
                "listing_machine": listing_machine_projected,
                "task_machine": task_machine_projected,
            }

            approve_listing_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=deployment.machine_asset,
                data=_contract_calldata(
                    web3=web3,
                    contract_address=deployment.machine_asset,
                    abi=ERC721_ABI,
                    fn_name="approve",
                    args=[deployment.machine_marketplace, int(listing_machine["onchain_machine_id"])],
                ),
            )
            create_listing_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=deployment.machine_marketplace,
                data=_contract_calldata(
                    web3=web3,
                    contract_address=deployment.machine_marketplace,
                    abi=MARKETPLACE_ABI,
                    fn_name="createListing",
                    args=[
                        int(listing_machine["onchain_machine_id"]),
                        deployment.usdt,
                        1_250_000,
                        4_102_444_800,
                    ],
                ),
            )
            active_listing = _wait_until(
                "listing projected",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/marketplace/listings").json()
                        if item["machine_id"] == listing_machine["id"] and item["state"] == "active"
                    ),
                    None,
                ),
                poller=poll_indexer,
            )
            approve_adapter_usdc_tx = _approve_erc20_max(
                web3=web3,
                token_address=deployment.usdc,
                owner_private_key=admin.private_key,
                spender=deployment.order_payment_router,
            )

            plan_resp = client.post(
                "/api/v1/chat/plans",
                json={
                    "user_id": buyer.user_id,
                    "chat_session_id": "chat-recovery-e2e",
                    "user_message": "Generate a final deliverable pack and return preview assets.",
                    "mode": "efficiency",
                    "input_files": ["brief.md"],
                },
            )
            plan_resp.raise_for_status()
            selected_plan = plan_resp.json()["recommended_plans"][0]

            order_resp = client.post(
                "/api/v1/orders",
                json={
                    "user_id": buyer.user_id,
                    "machine_id": task_machine["id"],
                    "chat_session_id": "chat-recovery-e2e",
                    "user_prompt": "Generate a final deliverable pack and return preview assets.",
                    "quoted_amount_cents": 1000,
                    "input_files": ["brief.md"],
                    "execution_strategy": "efficiency",
                    "selected_plan_id": selected_plan["plan_id"],
                },
            )
            order_resp.raise_for_status()
            order_payload = order_resp.json()

            payment_intent_resp = client.post(
                f"/api/v1/payments/orders/{order_payload['id']}/intent",
                json={"amount_cents": 1000, "currency": "USDC"},
            )
            payment_intent_resp.raise_for_status()
            payment_intent = payment_intent_resp.json()
            webhook_result = _build_hsp_webhook(
                client=client,
                payment_intent=payment_intent,
                amount_cents=1000,
                tx_signature="0xrecovery0000000000000000000000000000000000000000000000000000000001",
            )

            paid_order = _wait_until(
                "paid order projected",
                lambda: (
                    client.get(f"/api/v1/orders/{order_payload['id']}").json()
                    if client.get(f"/api/v1/orders/{order_payload['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PAID"
                    else None
                ),
                poller=poll_indexer,
            )
            mock_ready_resp = client.post(
                f"/api/v1/orders/{order_payload['id']}/mock-result-ready",
                json={"valid_preview": True},
            )
            mock_ready_resp.raise_for_status()
            preview_ready = _wait_until(
                "preview ready",
                lambda: (
                    client.get(f"/api/v1/orders/{order_payload['id']}").json()
                    if client.get(f"/api/v1/orders/{order_payload['id']}").json()["execution_metadata"].get("authoritative_order_status")
                    == "PREVIEW_READY"
                    else None
                ),
                poller=poll_indexer,
            )

            confirm_address, confirm_payload = _order_writer_call(
                modules,
                container=container,
                order_id=order_payload["id"],
                action="confirm",
            )
            confirm_tx = _send_transaction(
                web3=web3,
                private_key=buyer.private_key,
                to=confirm_address,
                data=confirm_payload["calldata"],
            )
            confirmed_order = _wait_until(
                "confirmed order",
                lambda: (
                    client.get(f"/api/v1/orders/{order_payload['id']}").json()
                    if client.get(f"/api/v1/orders/{order_payload['id']}").json()["state"] == "result_confirmed"
                    else None
                ),
                poller=poll_indexer,
            )

            claim_machine_address, claim_machine_payload = _machine_writer_call(
                modules,
                container=container,
                machine_id=task_machine["id"],
                action="claim_machine_revenue",
            )
            machine_claim_tx = _send_transaction(
                web3=web3,
                private_key=owner.private_key,
                to=claim_machine_address,
                data=claim_machine_payload["calldata"],
            )
            owner_revenue_after_claim = _wait_until(
                "owner revenue claimed",
                lambda: (
                    client.get(f"/api/v1/revenue/accounts/{owner.user_id}/overview").json()
                    if client.get(f"/api/v1/revenue/accounts/{owner.user_id}/overview").json()["claimed_pwr"] > 0
                    else None
                ),
                poller=poll_indexer,
            )

            report["transactions"] = {
                "approve_listing": approve_listing_tx,
                "create_listing": create_listing_tx,
                "approve_adapter_usdc": approve_adapter_usdc_tx,
                "hsp_webhook": webhook_result,
                "confirm_order": confirm_tx,
                "claim_machine_revenue": machine_claim_tx,
            }
            report["snapshots"]["before_db_wipe"] = {
                "active_listing": active_listing,
                "paid_order": paid_order,
                "preview_ready": preview_ready,
                "confirmed_order": confirmed_order,
                "state": _snapshot_state(client=client, buyer_user_id=buyer.user_id, owner_user_id=owner.user_id),
                "db_counts": _db_counts(modules),
                "payment_snapshot": _payment_snapshot(modules),
            }

        db_path = Path(DEFAULT_DB_PATH)
        if db_path.exists():
            db_path.unlink()

        modules = _import_backend_modules()
        rebuilt_app = modules["create_app"]()
        rebuilt_container = modules["get_container"]()
        rebuilt_real_indexer = rebuilt_container.onchain_indexer
        rebuilt_container.onchain_indexer = _ManualIndexerProxy()
        if not getattr(rebuilt_real_indexer, "status", None) or not rebuilt_real_indexer.status.enabled:
            raise RuntimeError(f"rebuilt_indexer_not_live:{getattr(rebuilt_real_indexer, 'status', None)}")

        def poll_rebuilt_indexer() -> None:
            outcome = rebuilt_real_indexer.poll_once()
            if outcome is not None:
                report.setdefault("rebuild_indexer_polls", []).append(
                    {
                        "from_block": outcome.from_block,
                        "to_block": outcome.to_block,
                        "applied": outcome.applied_events,
                        "duplicates": outcome.skipped_duplicates,
                        "last_scanned_block": outcome.cursor_advanced_to,
                    }
                )

        with TestClient(rebuilt_app) as client:
            rebuilt_listing = _wait_until(
                "listing rebuilt from chain",
                lambda: next(
                    (
                        item
                        for item in client.get("/api/v1/marketplace/listings").json()
                        if item["onchain_listing_id"] == active_listing["onchain_listing_id"] and item["state"] == "active"
                    ),
                    None,
                ),
                poller=poll_rebuilt_indexer,
                timeout_seconds=30.0,
            )
            rebuilt_orders = _wait_until(
                "orders rebuilt from chain",
                lambda: (
                    client.get("/api/v1/orders", params={"user_id": buyer.user_id}).json()
                    if client.get("/api/v1/orders", params={"user_id": buyer.user_id}).json()["items"]
                    else None
                ),
                poller=poll_rebuilt_indexer,
                timeout_seconds=30.0,
            )
            rebuilt_owner_revenue = _wait_until(
                "owner revenue rebuilt from chain",
                lambda: (
                    client.get(f"/api/v1/revenue/accounts/{owner.user_id}/overview").json()
                    if client.get(f"/api/v1/revenue/accounts/{owner.user_id}/overview").json()["claimed_pwr"] > 0
                    else None
                ),
                poller=poll_rebuilt_indexer,
                timeout_seconds=30.0,
            )
            rebuilt_machines = client.get("/api/v1/machines").json()
            rebuilt_state = _snapshot_state(client=client, buyer_user_id=buyer.user_id, owner_user_id=owner.user_id)
            rebuilt_counts = _db_counts(modules)
            rebuilt_payment_snapshot = _payment_snapshot(modules)

        report["snapshots"]["after_rebuild"] = {
            "rebuilt_listing": rebuilt_listing,
            "rebuilt_orders": rebuilt_orders,
            "rebuilt_owner_revenue": rebuilt_owner_revenue,
            "machines": rebuilt_machines,
            "state": rebuilt_state,
            "db_counts": rebuilt_counts,
            "payment_snapshot": rebuilt_payment_snapshot,
        }
        report["assertions"] = {
            "machines_rebuilt": rebuilt_counts["machines"] >= 2,
            "listing_rebuilt": rebuilt_counts["listings"] >= 1 and rebuilt_listing["payment_token_symbol"] == "USDT",
            "order_rebuilt": rebuilt_counts["orders"] >= 1 and rebuilt_orders["items"][0]["state"] == "result_confirmed",
            "payment_rebuilt": rebuilt_counts["payments"] >= 1 and rebuilt_payment_snapshot["payment"]["state"] == "succeeded",
            "settlement_rebuilt": rebuilt_counts["settlements"] >= 1,
            "revenue_rebuilt": rebuilt_counts["revenue_entries"] >= 1,
            "claim_rebuilt": rebuilt_counts["claim_records"] >= 1 and rebuilt_owner_revenue["claimed_pwr"] > 0,
        }
        if not all(report["assertions"].values()):
            raise RuntimeError(f"recovery_assertions_failed:{report['assertions']}")
    finally:
        DEFAULT_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if anvil_process is not None:
            anvil_process.terminate()
            try:
                anvil_process.wait(timeout=10)
            except Exception:
                anvil_process.kill()

    print(json.dumps(report["assertions"], ensure_ascii=False, indent=2))
    print(f"report={DEFAULT_REPORT_PATH}")


if __name__ == "__main__":
    main()
