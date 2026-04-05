import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app
from app.onchain.order_writer import OrderWriteResult, get_order_writer


class SpyOrderWriter:
    def __init__(self) -> None:
        self.mark_paid_calls: list[dict] = []

    def create_order(self, order):  # pragma: no cover
        return None

    def mark_preview_ready(self, order):  # pragma: no cover
        return None

    def confirm_result(self, order):  # pragma: no cover
        return None

    def settle_order(self, order, settlement):  # pragma: no cover
        return None

    def mark_order_paid(self, order, payment):
        self.mark_paid_calls.append({"order_id": order.id, "payment_id": payment.id})
        return None

    def build_direct_payment_intent(
        self,
        order,
        payment,
        *,
        pwr_amount=None,
        pricing_version=None,
        pwr_anchor_price_cents=None,
    ):
        currency = payment.currency.upper()
        if currency == "USDC":
            method_name = "payWithUSDCByAuthorization"
            signing_standard = "eip3009"
            payload = {
                "order_id": order.id,
                "payment_id": payment.id,
                "amount_cents": payment.amount_cents,
                "currency": currency,
                "signing_standard": signing_standard,
            }
        elif currency == "USDT":
            method_name = "payWithUSDT"
            signing_standard = "permit2"
            payload = {
                "order_id": order.id,
                "payment_id": payment.id,
                "amount_cents": payment.amount_cents,
                "currency": currency,
                "signing_standard": signing_standard,
            }
        else:
            method_name = "payWithPWR"
            signing_standard = "erc20_approve"
            payload = {
                "order_id": order.id,
                "payment_id": payment.id,
                "amount_cents": payment.amount_cents,
                "currency": currency,
                "pwr_amount": pwr_amount,
                "pricing_version": pricing_version,
                "pwr_anchor_price_cents": pwr_anchor_price_cents,
                "signing_standard": signing_standard,
            }
        return OrderWriteResult(
            tx_hash="0xintent",
            submitted_at=payment.created_at,
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name=method_name,
            idempotency_key="intent-key",
            payload=payload,
        )


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOrderWriter]:
    db_path = tmp_path / "direct-payments.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    spy_writer = SpyOrderWriter()
    app = create_app()
    app.dependency_overrides[get_order_writer] = lambda: spy_writer
    with TestClient(app) as test_client:
        yield test_client, spy_writer
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str, quoted_amount_cents: int = 1000) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Generate a landing page hero",
            "quoted_amount_cents": quoted_amount_cents,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_direct_payment_intent_returns_router_call_spec(client: tuple[TestClient, SpyOrderWriter]) -> None:
    test_client, _spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDC"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["provider"] == "onchain_router"
    assert payload["contract_name"] == "OrderPaymentRouter"
    assert payload["method_name"] == "payWithUSDCByAuthorization"
    assert payload["signing_standard"] == "eip3009"
    assert payload["submit_payload"]["currency"] == "USDC"
    assert payload["submit_payload"]["amount_cents"] == 1000


def test_create_direct_payment_intent_supports_pwr_when_anchor_exists(client: tuple[TestClient, SpyOrderWriter]) -> None:
    test_client, _spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "PWR"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["provider"] == "onchain_router"
    assert payload["contract_name"] == "OrderPaymentRouter"
    assert payload["method_name"] == "payWithPWR"
    assert payload["submit_payload"]["currency"] == "PWR"
    assert payload["submit_payload"]["pwr_amount"] == "36000000000000000000"
    assert payload["quote"]["pwr_anchor_price_cents"] == 25


def test_sync_onchain_payment_freezes_policy_without_duplicate_write_chain_call(client: tuple[TestClient, SpyOrderWriter]) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDT"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xabc123", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 200
    assert sync.json()["state"] == "succeeded"
    assert sync.json()["synced_onchain"] is True

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after.status_code == 200
    assert order_after.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []


def test_sync_onchain_pwr_payment_freezes_policy_without_duplicate_write_chain_call(client: tuple[TestClient, SpyOrderWriter]) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "PWR"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xpwr123", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 200
    assert sync.json()["state"] == "succeeded"
    assert sync.json()["synced_onchain"] is True

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after.status_code == 200
    assert order_after.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []
