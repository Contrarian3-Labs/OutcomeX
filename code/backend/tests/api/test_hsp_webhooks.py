import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt, get_onchain_broadcaster
from app.domain.models import Payment
from app.main import create_app
from app.onchain.order_writer import OrderWriteResult, get_order_writer
from app.onchain.tx_sender import get_onchain_transaction_sender


class SpyOrderWriter:
    def __init__(self) -> None:
        self.create_and_mark_paid_calls: list[dict] = []
        self.mark_paid_calls: list[dict] = []

    def create_order(self, order):  # pragma: no cover - route noise for this test
        return None

    def mark_preview_ready(self, order):  # pragma: no cover - route noise for this test
        return None

    def confirm_result(self, order):  # pragma: no cover - route noise for this test
        return None

    def settle_order(self, order, settlement):  # pragma: no cover - route noise for this test
        return None

    def create_order_and_mark_paid(self, order, payment, *, buyer_wallet_address):
        self.create_and_mark_paid_calls.append(
            {
                "order_id": order.id,
                "payment_id": payment.id,
                "buyer_wallet_address": buyer_wallet_address,
            }
        )
        return OrderWriteResult(
            tx_hash="0xcreatepaid",
            submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="createPaidOrderByAdapter",
            idempotency_key="writer-create-paid",
            payload={"buyer": buyer_wallet_address, "machine_id": order.machine_id, "amount": payment.amount_cents},
        )

    def mark_order_paid(self, order, payment):
        self.mark_paid_calls.append(
            {
                "order_id": order.id,
                "payment_id": payment.id,
                "beneficiary_user_id": order.settlement_beneficiary_user_id,
                "is_self_use": order.settlement_is_self_use,
                "is_dividend_eligible": order.settlement_is_dividend_eligible,
            }
        )
        return None


class SpyOnchainBroadcaster:
    def __init__(self) -> None:
        self.create_paid_calls: list[dict] = []

    def broadcast_create_paid_order(self, *, write_result):
        self.create_paid_calls.append(
            {
                "method_name": write_result.method_name,
                "buyer": write_result.payload["buyer"],
            }
        )
        return OnchainCreateOrderReceipt(
            onchain_order_id="oc_98001",
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:oc_98001:{write_result.tx_hash}",
            block_number=3330001,
        )


class SpyTransactionSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, write_result):
        self.calls.append({"method_name": write_result.method_name, "tx_hash": write_result.tx_hash})
        return OrderWriteResult(
            tx_hash="0xlivetx",
            submitted_at=write_result.submitted_at,
            chain_id=write_result.chain_id,
            contract_name=write_result.contract_name,
            contract_address=write_result.contract_address,
            method_name=write_result.method_name,
            idempotency_key=write_result.idempotency_key,
            payload=write_result.payload,
        )


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender]:
    db_path = tmp_path / "hsp-webhooks.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = json.dumps(
        {"user-2": "0x2222222222222222222222222222222222222222"}
    )
    reset_settings_cache()
    reset_container_cache()
    spy_writer = SpyOrderWriter()
    spy_broadcaster = SpyOnchainBroadcaster()
    spy_sender = SpyTransactionSender()
    app = create_app()
    app.dependency_overrides[get_order_writer] = lambda: spy_writer
    app.dependency_overrides[get_onchain_broadcaster] = lambda: spy_broadcaster
    app.dependency_overrides[get_onchain_transaction_sender] = lambda: spy_sender
    with TestClient(app) as test_client:
        yield test_client, spy_writer, spy_broadcaster, spy_sender
    reset_settings_cache()
    reset_container_cache()


def _sign_payload(payload: dict) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    timestamp = "1712233445"
    signature = hmac.new(
        b"dev-key",
        msg=f"{timestamp}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return body, {
        "x-hsp-signature": signature,
        "x-hsp-timestamp": timestamp,
        "content-type": "application/json",
    }


def _create_machine(client: TestClient, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str, user_id: str = "user-2", quoted_amount_cents: int = 500) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": user_id,
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Create a launch workflow",
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


def _create_payment_intent(client: TestClient, order_id: str, amount_cents: int = 500) -> dict:
    response = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": amount_cents, "currency": "usdc"},
    )
    assert response.status_code == 201
    return response.json()


def test_hsp_webhook_is_idempotent_and_freezes_settlement_policy(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, spy_writer, spy_broadcaster, spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])
    payload = {
        "event_id": "evt_1",
        "merchant_order_id": payment["merchant_order_id"],
        "flow_id": payment["flow_id"],
        "status": "completed",
        "amount_cents": 500,
        "currency": "USDC",
        "tx_hash": "0xabc123",
    }
    body, headers = _sign_payload(payload)

    first_response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert first_response.status_code == 200
    assert first_response.json() == {
        "payment_id": payment["payment_id"],
        "state": "succeeded",
        "duplicate": False,
    }

    duplicate_response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert duplicate_response.status_code == 200
    assert duplicate_response.json() == {
        "payment_id": payment["payment_id"],
        "state": "succeeded",
        "duplicate": True,
    }

    order_after_payment = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_payment.status_code == 200
    assert order_after_payment.json()["onchain_order_id"] == "oc_98001"
    assert order_after_payment.json()["create_order_tx_hash"] == "0xlivetx"
    assert order_after_payment.json()["create_order_event_id"] == "OrderCreated:oc_98001:0xlivetx"
    assert order_after_payment.json()["create_order_block_number"] == 3330001
    assert order_after_payment.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after_payment.json()["settlement_is_self_use"] is False
    assert order_after_payment.json()["settlement_is_dividend_eligible"] is True
    assert spy_writer.create_and_mark_paid_calls == [
        {
            "order_id": order["id"],
            "payment_id": payment["payment_id"],
            "buyer_wallet_address": "0x2222222222222222222222222222222222222222",
        }
    ]
    assert spy_writer.mark_paid_calls == []
    assert spy_sender.calls == [{"method_name": "createPaidOrderByAdapter", "tx_hash": "0xcreatepaid"}]
    assert spy_broadcaster.create_paid_calls == [
        {
            "method_name": "createPaidOrderByAdapter",
            "buyer": "0x2222222222222222222222222222222222222222",
        }
    ]

    with get_container().session_factory() as session:
        persisted_payment = session.get(Payment, payment["payment_id"])
        assert persisted_payment is not None
        assert persisted_payment.callback_event_id == "evt_1"
        assert persisted_payment.callback_state == "completed"
        assert persisted_payment.callback_tx_hash == "0xabc123"


def test_hsp_webhook_rejects_invalid_signatures(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])

    response = test_client.post(
        "/api/v1/payments/hsp/webhooks",
        content=json.dumps(
            {
                "event_id": "evt_invalid",
                "merchant_order_id": payment["merchant_order_id"],
                "flow_id": payment["flow_id"],
                "status": "completed",
                "amount_cents": 500,
                "currency": "USDC",
            }
        ),
        headers={
            "x-hsp-signature": "bad-signature",
            "x-hsp-timestamp": "1712233445",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid HSP signature"


def test_hsp_webhook_rejects_unresolved_buyer_wallet(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, spy_writer, spy_broadcaster, spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"], user_id="user-unmapped")
    payment = _create_payment_intent(test_client, order_id=order["id"])
    payload = {
        "event_id": "evt_unmapped",
        "merchant_order_id": payment["merchant_order_id"],
        "flow_id": payment["flow_id"],
        "status": "completed",
        "amount_cents": 500,
        "currency": "USDC",
        "tx_hash": "0xdeadbeef",
    }
    body, headers = _sign_payload(payload)

    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Buyer wallet address unresolved for HSP settlement"
    assert spy_writer.create_and_mark_paid_calls == []
    assert spy_broadcaster.create_paid_calls == []
    assert spy_sender.calls == []


def test_hsp_payment_intent_requires_exact_quote_and_single_active_intent(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"], quoted_amount_cents=500)

    mismatch = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 400, "currency": "usdc"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == "HSP payment amount must match quoted order amount"

    first = _create_payment_intent(test_client, order_id=order["id"], amount_cents=500)
    second = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "usdc"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "An active HSP payment already exists for this order"
    assert first["payment_id"]


def test_hsp_webhook_rejects_terminal_state_downgrade_after_success(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])

    success_payload = {
        "event_id": "evt_success",
        "merchant_order_id": payment["merchant_order_id"],
        "flow_id": payment["flow_id"],
        "status": "completed",
        "amount_cents": 500,
        "currency": "USDC",
        "tx_hash": "0xok",
    }
    success_body, success_headers = _sign_payload(success_payload)
    success_response = test_client.post("/api/v1/payments/hsp/webhooks", content=success_body, headers=success_headers)
    assert success_response.status_code == 200

    failed_payload = {
        "event_id": "evt_failed_late",
        "merchant_order_id": payment["merchant_order_id"],
        "flow_id": payment["flow_id"],
        "status": "failed",
        "amount_cents": 500,
        "currency": "USDC",
        "tx_hash": "0xfail",
    }
    failed_body, failed_headers = _sign_payload(failed_payload)
    failed_response = test_client.post("/api/v1/payments/hsp/webhooks", content=failed_body, headers=failed_headers)
    assert failed_response.status_code == 409
    assert failed_response.json()["detail"] == "Payment is already in terminal state"
