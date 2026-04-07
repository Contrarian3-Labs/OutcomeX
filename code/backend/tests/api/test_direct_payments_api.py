import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.domain.enums import PaymentState
from app.integrations.onchain_payment_verifier import OnchainPaymentVerificationResult, get_onchain_payment_verifier
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
            method_name = "createOrderAndPayWithUSDC"
            signing_standard = "eip3009"
            payload = {
                "client_order_id": order.id,
                "machine_id": order.machine_id,
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
                "currency": currency,
                "signing_standard": signing_standard,
            }
        elif currency == "USDT":
            method_name = "createOrderAndPayWithUSDT"
            signing_standard = "permit2"
            payload = {
                "client_order_id": order.id,
                "machine_id": order.machine_id,
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
                "currency": currency,
                "signing_standard": signing_standard,
            }
        else:
            method_name = "createOrderAndPayWithPWR"
            signing_standard = "erc20_approve"
            payload = {
                "client_order_id": order.id,
                "machine_id": order.machine_id,
                "payment_id": payment.id,
                "gross_amount_cents": order.quoted_amount_cents,
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


class SpyOnchainPaymentVerifier:
    def __init__(self) -> None:
        self.results: dict[str, OnchainPaymentVerificationResult] = {}

    def set_result(
        self,
        *,
        tx_hash: str,
        verification: OnchainPaymentVerificationResult,
    ) -> None:
        self.results[tx_hash.lower()] = verification

    def verify_payment(
        self,
        *,
        tx_hash: str,
        wallet_address: str | None,
        order,
        payment,
    ) -> OnchainPaymentVerificationResult:
        return self.results.get(
            tx_hash.lower(),
            OnchainPaymentVerificationResult(
                matched=False,
                state=PaymentState.FAILED,
                tx_hash=tx_hash,
                event_id=f"onchain:{tx_hash.lower()}",
                reason="unconfigured_tx_hash",
                evidence_order_id=None,
                evidence_amount_cents=None,
                evidence_currency=None,
                evidence_wallet_address=wallet_address,
                evidence_create_order_tx_hash=None,
                evidence_create_order_event_id=None,
                evidence_create_order_block_number=None,
            ),
        )


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier]:
    db_path = tmp_path / "direct-payments.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()
    spy_writer = SpyOrderWriter()
    spy_verifier = SpyOnchainPaymentVerifier()
    app = create_app()
    app.dependency_overrides[get_order_writer] = lambda: spy_writer
    app.dependency_overrides[get_onchain_payment_verifier] = lambda: spy_verifier
    with TestClient(app) as test_client:
        yield test_client, spy_writer, spy_verifier
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
    payload = response.json()
    assert payload["onchain_order_id"] is None
    assert payload["create_order_tx_hash"] is None
    assert payload["create_order_event_id"] is None
    assert payload["create_order_block_number"] is None
    return payload


def test_create_direct_payment_intent_returns_router_call_spec(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, _spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDC"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["provider"] == "onchain_router"
    assert payload["order_id"] == order["id"]
    assert payload["contract_name"] == "OrderPaymentRouter"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000134"
    assert payload["chain_id"] == 133
    assert payload["method_name"] == "createOrderAndPayWithUSDC"
    assert payload["signing_standard"] == "eip3009"
    assert payload["calldata"].startswith("0xc73f27f1")
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"
    assert payload["submit_payload"]["currency"] == "USDC"
    assert payload["submit_payload"]["gross_amount_cents"] == 1000
    assert payload["submit_payload"]["machine_id"] == machine["id"]
    assert "order_id" not in payload["submit_payload"]


def test_create_direct_payment_intent_supports_pwr_when_anchor_exists(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, _spy_verifier = client
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
    assert payload["method_name"] == "createOrderAndPayWithPWR"
    assert payload["calldata"].startswith("0x321a55a2")
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"
    assert payload["submit_payload"]["currency"] == "PWR"
    assert payload["submit_payload"]["pwr_amount"] == "36000000000000000000"
    assert payload["quote"]["pwr_anchor_price_cents"] == 25


def test_create_direct_payment_intent_returns_wallet_envelope_for_usdt(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, _spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDT"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["method_name"] == "createOrderAndPayWithUSDT"
    assert payload["signing_standard"] == "permit2"
    assert payload["calldata"].startswith("0x3d961057")
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"


def test_sync_onchain_payment_freezes_policy_without_duplicate_write_chain_call(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDT"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]
    spy_verifier.set_result(
        tx_hash="0xabc123",
        verification=OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash="0xabc123",
            event_id="onchain:0xabc123",
            reason=None,
            evidence_order_id="oc_42001",
            evidence_amount_cents=1000,
            evidence_currency="USDT",
            evidence_wallet_address="0xbuyer",
            evidence_create_order_tx_hash="0xabc123",
            evidence_create_order_event_id="OrderCreated:oc_42001:0xabc123",
            evidence_create_order_block_number=2001001,
        ),
    )

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xabc123", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 200
    assert sync.json()["state"] == "succeeded"
    assert sync.json()["synced_onchain"] is True

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after.status_code == 200
    assert order_after.json()["onchain_order_id"] == "oc_42001"
    assert order_after.json()["create_order_tx_hash"] == "0xabc123"
    assert order_after.json()["create_order_event_id"] == "OrderCreated:oc_42001:0xabc123"
    assert order_after.json()["create_order_block_number"] == 2001001
    assert order_after.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []


def test_sync_onchain_pwr_payment_freezes_policy_without_duplicate_write_chain_call(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "PWR"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]
    spy_verifier.set_result(
        tx_hash="0xpwr123",
        verification=OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.SUCCEEDED,
            tx_hash="0xpwr123",
            event_id="onchain:0xpwr123",
            reason=None,
            evidence_order_id="oc_53001",
            evidence_amount_cents=1000,
            evidence_currency="PWR",
            evidence_wallet_address="0xbuyer",
            evidence_create_order_tx_hash="0xpwr123",
            evidence_create_order_event_id="OrderCreated:oc_53001:0xpwr123",
            evidence_create_order_block_number=2002002,
        ),
    )

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xpwr123", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 200
    assert sync.json()["state"] == "succeeded"
    assert sync.json()["synced_onchain"] is True

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after.status_code == 200
    assert order_after.json()["onchain_order_id"] == "oc_53001"
    assert order_after.json()["create_order_tx_hash"] == "0xpwr123"
    assert order_after.json()["create_order_event_id"] == "OrderCreated:oc_53001:0xpwr123"
    assert order_after.json()["create_order_block_number"] == 2002002
    assert order_after.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []


def test_sync_onchain_rejects_unverified_event_mismatch(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDC"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]
    spy_verifier.set_result(
        tx_hash="0xmismatch",
        verification=OnchainPaymentVerificationResult(
            matched=False,
            state=PaymentState.FAILED,
            tx_hash="0xmismatch",
            event_id="onchain:0xmismatch",
            reason="order_id_mismatch",
            evidence_order_id="wrong-onchain-order",
            evidence_amount_cents=1000,
            evidence_currency="USDC",
            evidence_wallet_address="0xbuyer",
            evidence_create_order_tx_hash=None,
            evidence_create_order_event_id=None,
            evidence_create_order_block_number=None,
        ),
    )

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xmismatch", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 409
    assert sync.json()["detail"] == "Onchain evidence verification failed: order_id_mismatch"


def test_sync_onchain_uses_verifier_state_instead_of_caller_state(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    intent = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDC"},
    )
    assert intent.status_code == 201
    payment_id = intent.json()["payment_id"]
    spy_verifier.set_result(
        tx_hash="0xfailed",
        verification=OnchainPaymentVerificationResult(
            matched=True,
            state=PaymentState.FAILED,
            tx_hash="0xfailed",
            event_id="onchain:0xfailed",
            reason="reverted",
            evidence_order_id="oc_unused_failed",
            evidence_amount_cents=1000,
            evidence_currency="USDC",
            evidence_wallet_address="0xbuyer",
            evidence_create_order_tx_hash=None,
            evidence_create_order_event_id=None,
            evidence_create_order_block_number=None,
        ),
    )

    sync = test_client.post(
        f"/api/v1/payments/{payment_id}/sync-onchain",
        json={"state": "succeeded", "tx_hash": "0xfailed", "wallet_address": "0xbuyer"},
    )
    assert sync.status_code == 200
    assert sync.json()["state"] == "failed"
