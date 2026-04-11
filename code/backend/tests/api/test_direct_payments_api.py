import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import PaymentState
from app.domain.models import Order, Payment
from app.integrations.onchain_payment_verifier import OnchainPaymentVerificationResult, get_onchain_payment_verifier
from app.main import create_app
from app.onchain.order_writer import OrderWriteResult, get_order_writer


class SpyOrderWriter:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.mark_paid_calls: list[dict] = []

    def create_order(self, order, *, buyer_wallet_address, gross_amount_override=None):
        gross_amount = order.quoted_amount_cents if gross_amount_override is None else gross_amount_override
        self.create_calls.append({"order_id": order.id, "buyer_wallet_address": buyer_wallet_address, "gross_amount": gross_amount})
        return OrderWriteResult(
            tx_hash="0xcreateorder",
            submitted_at=order.created_at,
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="createOrderByAdapter",
            idempotency_key=f"create-{order.id}",
            payload={
                "buyer": buyer_wallet_address,
                "machine_id": order.machine_id,
                "gross_amount": gross_amount,
            },
        )

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
            method_name = "payWithPWR"
            signing_standard = "erc20_approve"
            payload = {
                "client_order_id": order.id,
                "order_id": order.onchain_order_id or "1",
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
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = '{"user-1":"0x00000000000000000000000000000000000000aa"}'
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


def _seed_legacy_direct_payment(
    *,
    order_id: str,
    currency: str,
    spy_writer: SpyOrderWriter,
    wallet_address: str | None = None,
) -> str:
    with get_container().session_factory() as session:
        order = session.get(Order, order_id)
        assert order is not None
        payment = Payment(
            order_id=order.id,
            provider="onchain_router",
            amount_cents=order.quoted_amount_cents,
            currency=currency,
            state=PaymentState.PENDING,
            merchant_order_id=order.id,
            flow_id=f"legacy-{currency.lower()}-{order.id}",
        )
        session.add(payment)
        session.flush()

        direct_intent = spy_writer.build_direct_payment_intent(order, payment)
        if currency == "USDC":
            signing_request = {
                "message": {
                    "validAfter": "1712233000",
                    "validBefore": "1712235000",
                    "nonce": "0x" + "ab" * 32,
                    "from": (wallet_address or "0x00000000000000000000000000000000000000aa").lower(),
                }
            }
        elif currency == "USDT":
            signing_request = {
                "message": {
                    "nonce": "12345",
                    "deadline": "1712235000",
                }
            }
        else:
            signing_request = None
        payment.provider_reference = direct_intent.method_name
        payment.provider_payload = {
            "direct_intent_payload": direct_intent.payload,
            "signing_request": signing_request,
        }
        session.add(payment)
        session.commit()
        return payment.id


def test_create_direct_payment_intent_rejects_legacy_stablecoin_checkout(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, _spy_writer, _spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])

    usdc_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDC", "wallet_address": "0x00000000000000000000000000000000000000aa"},
    )
    assert usdc_response.status_code == 409
    assert usdc_response.json()["detail"] == "Direct stablecoin checkout is legacy-only; use the HSP payment intent route"

    usdt_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/direct-intent",
        json={"amount_cents": 1000, "currency": "USDT"},
    )
    assert usdt_response.status_code == 409
    assert usdt_response.json()["detail"] == "Direct stablecoin checkout is legacy-only; use the HSP payment intent route"


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
    assert payload["method_name"] == "payWithPWR"
    assert payload["finalize_required"] is False
    assert payload["calldata"].startswith("0xd4099cc2")
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"
    assert payload["submit_payload"]["currency"] == "PWR"
    assert payload["submit_payload"]["order_id"] is not None
    assert payload["submit_payload"]["pwr_amount"] == "40000000000000000000"
    assert _spy_writer.create_calls[-1]["gross_amount"] == 40_000_000_000_000_000_000
    assert payload["quote"]["pwr_anchor_price_cents"] == 25
    anchored_order = test_client.get(f"/api/v1/orders/{order['id']}").json()
    assert anchored_order["onchain_order_id"] is not None
    assert anchored_order["create_order_tx_hash"] == "0xcreateorder"


def test_finalize_usdc_direct_payment_intent_returns_wallet_envelope(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, _spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    payment_id = _seed_legacy_direct_payment(
        order_id=order["id"],
        currency="USDC",
        spy_writer=spy_writer,
        wallet_address="0x00000000000000000000000000000000000000aa",
    )

    response = test_client.post(
        f"/api/v1/payments/{payment_id}/finalize-intent",
        json={"signature": "0x" + ("11" * 32) + ("22" * 32) + "1b"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["method_name"] == "createOrderAndPayWithUSDC"
    assert payload["calldata"].startswith("0xc73f27f1")
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"
    assert payload["submit_payload"]["v"] == 27
    assert payload["submit_payload"]["r"] == "0x" + "11" * 32
    assert payload["submit_payload"]["s"] == "0x" + "22" * 32


def test_finalize_usdt_direct_payment_intent_returns_wallet_envelope(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, _spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    payment_id = _seed_legacy_direct_payment(order_id=order["id"], currency="USDT", spy_writer=spy_writer)

    response = test_client.post(
        f"/api/v1/payments/{payment_id}/finalize-intent",
        json={"signature": "0x" + "22" * 65},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["method_name"] == "createOrderAndPayWithUSDT"
    assert payload["calldata"].startswith("0x3d961057")
    assert payload["submit_payload"]["signature"] == "0x" + "22" * 65
    assert payload["submit_payload"]["to"] == payload["contract_address"]
    assert payload["submit_payload"]["data"] == payload["calldata"]
    assert payload["submit_payload"]["value"] == "0x0"


def test_sync_onchain_payment_marks_paid_projection_and_records_correlation(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    payment_id = _seed_legacy_direct_payment(order_id=order["id"], currency="USDT", spy_writer=spy_writer)
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
    assert order_after.json()["execution_metadata"]["last_payment_tx_hash"] == "0xabc123"
    assert order_after.json()["execution_metadata"]["authoritative_paid_projection"] is True
    assert order_after.json()["execution_metadata"]["authoritative_order_status"] == "PAID"
    assert order_after.json()["payment_state"] == "succeeded"
    assert order_after.json()["is_cancelled"] is False
    assert order_after.json()["machine_is_available"] is True
    assert order_after.json()["settlement_beneficiary_user_id"] == machine["owner_user_id"]
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []


def test_sync_onchain_pwr_payment_marks_paid_projection_and_records_correlation(
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
    assert order_after.json()["onchain_order_id"] is not None
    assert order_after.json()["create_order_tx_hash"] == "0xcreateorder"
    assert order_after.json()["create_order_event_id"] is not None
    assert order_after.json()["create_order_block_number"] is not None
    assert order_after.json()["execution_metadata"]["last_payment_tx_hash"] == "0xpwr123"
    assert order_after.json()["execution_metadata"]["authoritative_paid_projection"] is True
    assert order_after.json()["execution_metadata"]["authoritative_order_status"] == "PAID"
    assert order_after.json()["payment_state"] == "succeeded"
    assert order_after.json()["is_cancelled"] is False
    assert order_after.json()["machine_is_available"] is True
    assert order_after.json()["settlement_beneficiary_user_id"] == machine["owner_user_id"]
    assert order_after.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.mark_paid_calls == []


def test_sync_onchain_rejects_unverified_event_mismatch(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    payment_id = _seed_legacy_direct_payment(
        order_id=order["id"],
        currency="USDC",
        spy_writer=spy_writer,
        wallet_address="0x00000000000000000000000000000000000000aa",
    )
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


def test_sync_onchain_rejects_non_successful_verified_receipt(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainPaymentVerifier],
) -> None:
    test_client, spy_writer, spy_verifier = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine["id"])
    payment_id = _seed_legacy_direct_payment(
        order_id=order["id"],
        currency="USDC",
        spy_writer=spy_writer,
        wallet_address="0x00000000000000000000000000000000000000aa",
    )
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
    assert sync.status_code == 409
    assert sync.json()["detail"] == "Onchain receipt did not confirm a successful payment"
