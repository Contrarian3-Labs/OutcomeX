import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt, get_onchain_broadcaster
from app.domain.models import Order, Payment
from app.main import create_app
from app.onchain.order_writer import OrderWriteResult, get_order_writer
from app.onchain.tx_sender import get_onchain_transaction_sender


class SpyOrderWriter:
    def __init__(self) -> None:
        self.create_order_calls: list[dict] = []
        self.pay_order_by_adapter_calls: list[dict] = []
        self.mark_paid_calls: list[dict] = []

    def create_order(self, order, *, buyer_wallet_address, gross_amount_override=None):
        self.create_order_calls.append(
            {
                "order_id": order.id,
                "buyer_wallet_address": buyer_wallet_address,
            }
        )
        return OrderWriteResult(
            tx_hash="0xcreateorder",
            submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="createOrderByAdapter",
            idempotency_key="writer-create-order",
            payload={"buyer": buyer_wallet_address, "machine_id": order.machine_id, "gross_amount": order.quoted_amount_cents},
        )

    def mark_preview_ready(self, order):  # pragma: no cover - route noise for this test
        return None

    def confirm_result(self, order):  # pragma: no cover - route noise for this test
        return None

    def settle_order(self, order, settlement):  # pragma: no cover - route noise for this test
        return None

    def pay_order_by_adapter(self, order, payment):
        self.pay_order_by_adapter_calls.append(
            {
                "order_id": order.id,
                "payment_id": payment.id,
            }
        )
        return OrderWriteResult(
            tx_hash="0xpaybyadapter",
            submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="payOrderByAdapter",
            idempotency_key=f"writer-pay-order-{payment.id}",
            payload={
                "order_id": order.onchain_order_id,
                "amount": payment.amount_cents,
                "payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
            },
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
        self.create_order_calls: list[dict] = []
        self.create_paid_calls: list[dict] = []

    def broadcast_create_order(self, *, write_result):
        sequence = len(self.create_order_calls) + 1
        order_id = f"97{sequence:03d}"
        self.create_order_calls.append(
            {
                "method_name": write_result.method_name,
                "buyer": write_result.payload["buyer"],
            }
        )
        return OnchainCreateOrderReceipt(
            onchain_order_id=order_id,
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:{order_id}:{write_result.tx_hash}",
            block_number=3330000 + sequence,
        )

    def broadcast_create_paid_order(self, *, write_result):
        self.create_paid_calls.append(
            {
                "method_name": write_result.method_name,
                "order_id": write_result.payload.get("order_id"),
            }
        )
        return OnchainCreateOrderReceipt(
            onchain_order_id="98001",
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:98001:{write_result.tx_hash}",
            block_number=3330001,
        )


class SpyTransactionSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, write_result):
        self.calls.append({"method_name": write_result.method_name, "tx_hash": write_result.tx_hash})
        return OrderWriteResult(
            tx_hash=write_result.tx_hash,
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
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"dev-key",
        msg=f"{timestamp}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return body, {
        "x-signature": f"t={timestamp},v1={signature}",
        "content-type": "application/json",
    }


def _webhook_payload(
    payment: dict,
    *,
    status: str,
    amount_cents: int = 500,
    currency: str = "USDC",
    tx_signature: str | None = None,
    request_id: str = "req_1",
) -> dict:
    payload = {
        "event_type": "payment",
        "payment_request_id": payment["provider_reference"],
        "request_id": request_id,
        "cart_mandate_id": payment["merchant_order_id"],
        "payer_address": "0x1234567890abcdef1234567890abcdef12345678",
        "amount": str(amount_cents * 10_000),
        "token": currency,
        "token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        "chain": "eip155:133",
        "network": "hashkey-testnet",
        "status": status,
        "created_at": "2026-04-08T12:00:00Z",
    }
    if tx_signature:
        payload["tx_signature"] = tx_signature
        payload["completed_at"] = "2026-04-08T12:00:30Z"
    return payload


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


def _create_payment_intent(
    client: TestClient,
    order_id: str,
    amount_cents: int = 500,
    *,
    currency: str = "usdc",
) -> dict:
    response = client.post(
        f"/api/v1/payments/orders/{order_id}/intent",
        json={"amount_cents": amount_cents, "currency": currency},
    )
    assert response.status_code == 201
    return response.json()


def _anchor_order(
    *,
    order_id: str,
    onchain_order_id: str,
    create_order_tx_hash: str,
    create_order_event_id: str,
    create_order_block_number: int,
) -> None:
    with get_container().session_factory() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.onchain_order_id = onchain_order_id
        order.create_order_tx_hash = create_order_tx_hash
        order.create_order_event_id = create_order_event_id
        order.create_order_block_number = create_order_block_number
        session.add(order)
        session.commit()


def test_hsp_webhook_is_idempotent_and_freezes_settlement_policy(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, spy_writer, spy_broadcaster, spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])
    payload = _webhook_payload(payment, status="payment-successful", currency="USDC", tx_signature="0xabc123", request_id="evt_1")
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
    assert order_after_payment.json()["onchain_order_id"] == "97001"
    assert order_after_payment.json()["create_order_tx_hash"] == "0xcreateorder"
    assert order_after_payment.json()["create_order_event_id"] == "OrderCreated:97001:0xcreateorder"
    assert order_after_payment.json()["create_order_block_number"] == 3330001
    assert order_after_payment.json()["settlement_beneficiary_user_id"] == "owner-1"
    assert order_after_payment.json()["settlement_is_self_use"] is False
    assert order_after_payment.json()["settlement_is_dividend_eligible"] is True
    assert order_after_payment.json()["execution_metadata"]["authoritative_order_status"] == "PAID"
    assert order_after_payment.json()["execution_metadata"]["authoritative_paid_projection"] is True
    assert order_after_payment.json()["execution_metadata"]["authoritative_order_event_id"] == "OrderCreated:98001:0xpaybyadapter"
    assert order_after_payment.json()["payment_state"] == "succeeded"
    assert order_after_payment.json()["is_cancelled"] is False
    assert order_after_payment.json()["machine_is_available"] is True
    assert spy_writer.create_order_calls == [
        {
            "order_id": order["id"],
            "buyer_wallet_address": "0x2222222222222222222222222222222222222222",
        }
    ]
    assert spy_writer.pay_order_by_adapter_calls == [
        {
            "order_id": order["id"],
            "payment_id": payment["payment_id"],
        }
    ]
    assert spy_writer.mark_paid_calls == []
    assert spy_sender.calls == [
        {"method_name": "createOrderByAdapter", "tx_hash": "0xcreateorder"},
        {"method_name": "payOrderByAdapter", "tx_hash": "0xpaybyadapter"},
    ]
    assert spy_broadcaster.create_order_calls == [
        {
            "method_name": "createOrderByAdapter",
            "buyer": "0x2222222222222222222222222222222222222222",
        }
    ]
    assert spy_broadcaster.create_paid_calls == [
        {
            "method_name": "payOrderByAdapter",
            "order_id": "97001",
        }
    ]

    with get_container().session_factory() as session:
        persisted_payment = session.get(Payment, payment["payment_id"])
        assert persisted_payment is not None
        assert persisted_payment.callback_event_id == "evt_1"
        assert persisted_payment.callback_state == "payment-successful"
        assert persisted_payment.callback_tx_hash == "0xabc123"


def test_hsp_webhook_marks_authoritative_paid_projection_when_order_is_already_anchored(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, spy_writer, spy_broadcaster, spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])
    _anchor_order(
        order_id=order["id"],
        onchain_order_id="97001",
        create_order_tx_hash="0xexistingtx",
        create_order_event_id="OrderCreated:97001:0xexistingtx",
        create_order_block_number=3330000,
    )
    payload = _webhook_payload(
        payment,
        status="payment-successful",
        currency="USDC",
        tx_signature="0xexistingpaid",
        request_id="evt_existing_paid",
    )
    body, headers = _sign_payload(payload)

    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)

    assert response.status_code == 200
    order_after_payment = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_payment.status_code == 200
    assert order_after_payment.json()["onchain_order_id"] == "97001"
    assert order_after_payment.json()["create_order_tx_hash"] == "0xexistingtx"
    assert order_after_payment.json()["execution_metadata"]["authoritative_order_status"] == "PAID"
    assert order_after_payment.json()["execution_metadata"]["authoritative_paid_projection"] is True
    assert order_after_payment.json()["execution_metadata"]["authoritative_order_event_id"] == "OrderCreated:98001:0xpaybyadapter"
    assert spy_writer.create_order_calls == [
        {
            "order_id": order["id"],
            "buyer_wallet_address": "0x2222222222222222222222222222222222222222",
        }
    ]
    assert spy_writer.pay_order_by_adapter_calls == [
        {
            "order_id": order["id"],
            "payment_id": payment["payment_id"],
        }
    ]
    assert spy_broadcaster.create_paid_calls == [
        {
            "method_name": "payOrderByAdapter",
            "order_id": "97001",
        }
    ]
    assert spy_sender.calls == [
        {"method_name": "createOrderByAdapter", "tx_hash": "0xcreateorder"},
        {"method_name": "payOrderByAdapter", "tx_hash": "0xpaybyadapter"},
    ]


def test_hsp_webhook_preserves_concurrent_projection_metadata(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, spy_writer, _spy_broadcaster, spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])

    class ConcurrentProjectionBroadcaster(SpyOnchainBroadcaster):
        def broadcast_create_paid_order(self, *, write_result):
            receipt = super().broadcast_create_paid_order(write_result=write_result)
            with get_container().session_factory() as session:
                persisted_order = session.get(Order, order["id"])
                assert persisted_order is not None
                metadata = dict(persisted_order.execution_metadata or {})
                metadata["concurrent_projection_marker"] = "kept"
                persisted_order.execution_metadata = metadata
                session.add(persisted_order)
                session.commit()
            return receipt

    broadcaster = ConcurrentProjectionBroadcaster()
    app = test_client.app
    app.dependency_overrides[get_onchain_broadcaster] = lambda: broadcaster
    try:
        payload = _webhook_payload(
            payment,
            status="payment-successful",
            currency="USDC",
            tx_signature="0xpreservemeta",
            request_id="evt_preserve_meta",
        )
        body, headers = _sign_payload(payload)
        response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    finally:
        app.dependency_overrides.pop(get_onchain_broadcaster, None)

    assert response.status_code == 200
    order_after_payment = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_payment.status_code == 200
    metadata = order_after_payment.json()["execution_metadata"]
    assert metadata["concurrent_projection_marker"] == "kept"
    assert metadata["authoritative_order_status"] == "PAID"
    assert metadata["authoritative_paid_projection"] is True
    assert spy_writer.pay_order_by_adapter_calls == [
        {
            "order_id": order["id"],
            "payment_id": payment["payment_id"],
        }
    ]
    assert broadcaster.create_paid_calls == [
        {
            "method_name": "payOrderByAdapter",
            "order_id": "97001",
        }
    ]
    assert spy_sender.calls == [
        {"method_name": "createOrderByAdapter", "tx_hash": "0xcreateorder"},
        {"method_name": "payOrderByAdapter", "tx_hash": "0xpaybyadapter"},
    ]


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
                **_webhook_payload(payment, status="payment-successful", currency="USDC", request_id="evt_invalid"),
            }
        ),
        headers={
            "x-signature": "t=1712233445,v1=bad-signature",
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
    response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "usdc"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Buyer wallet address unresolved for HSP settlement"
    assert spy_writer.create_order_calls == []
    assert spy_writer.pay_order_by_adapter_calls == []
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


def test_hsp_payment_intent_supports_usdt_checkout_and_success_webhook(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"], quoted_amount_cents=500)
    payment = _create_payment_intent(test_client, order_id=order["id"], amount_cents=500, currency="usdt")

    assert payment["provider"] == "hsp"

    payload = _webhook_payload(
        payment,
        status="payment-successful",
        currency="USDT",
        tx_signature="0xusdtok",
        request_id="evt_usdt_success",
    )
    body, headers = _sign_payload(payload)

    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)

    assert response.status_code == 200
    order_after_payment = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after_payment.status_code == 200
    assert order_after_payment.json()["latest_success_payment_currency"] == "USDT"
    assert order_after_payment.json()["execution_metadata"]["authoritative_paid_projection"] is True


def test_hsp_payment_intent_defaults_to_usdc_and_rejects_non_stablecoin_checkout_currency(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    unsupported_order = _create_order(test_client, machine_id=machine["id"], quoted_amount_cents=500)
    default_order = _create_order(test_client, machine_id=machine["id"], quoted_amount_cents=500)

    unsupported_currency = test_client.post(
        f"/api/v1/payments/orders/{unsupported_order['id']}/intent",
        json={"amount_cents": 500, "currency": "PWR"},
    )
    assert unsupported_currency.status_code == 400
    assert unsupported_currency.json()["detail"] == "HSP checkout only supports USDC or USDT stablecoins"

    default_currency = test_client.post(
        f"/api/v1/payments/orders/{default_order['id']}/intent",
        json={"amount_cents": 500},
    )
    assert default_currency.status_code == 201
    assert default_currency.json()["provider"] == "hsp"
    assert default_currency.json()["checkout_url"]


def test_payment_openapi_marks_hsp_checkout_as_formal_stablecoin_route(client) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client

    response = test_client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    checkout_operation = payload["paths"]["/api/v1/payments/orders/{order_id}/intent"]["post"]
    direct_operation = payload["paths"]["/api/v1/payments/orders/{order_id}/direct-intent"]["post"]
    assert "hsp" in checkout_operation["summary"].lower()
    assert "stablecoin" in checkout_operation["description"].lower()
    assert direct_operation["deprecated"] is True
    assert "legacy" in direct_operation["description"].lower()
    assert "hsp" in direct_operation["description"].lower()


def test_hsp_webhook_rejects_terminal_state_downgrade_after_success(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])

    success_payload = _webhook_payload(
        payment,
        status="payment-successful",
        currency="USDC",
        tx_signature="0xok",
        request_id="evt_success",
    )
    success_body, success_headers = _sign_payload(success_payload)
    success_response = test_client.post("/api/v1/payments/hsp/webhooks", content=success_body, headers=success_headers)
    assert success_response.status_code == 200

    failed_payload = _webhook_payload(
        payment,
        status="payment-failed",
        currency="USDC",
        tx_signature="0xfail",
        request_id="evt_failed_late",
    )
    failed_body, failed_headers = _sign_payload(failed_payload)
    failed_response = test_client.post("/api/v1/payments/hsp/webhooks", content=failed_body, headers=failed_headers)
    assert failed_response.status_code == 409
    assert failed_response.json()["detail"] == "Payment is already in terminal state"


def test_hsp_webhook_rejects_success_without_tx_signature(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment = _create_payment_intent(test_client, order_id=order["id"])

    payload = _webhook_payload(
        payment,
        status="payment-successful",
        currency="USDC",
        tx_signature=None,
        request_id="evt_missing_tx",
    )
    body, headers = _sign_payload(payload)
    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Successful HSP webhook must include tx signature"


def test_hsp_webhook_rejects_success_with_reused_tx_signature_across_payments(
    client: tuple[TestClient, SpyOrderWriter, SpyOnchainBroadcaster, SpyTransactionSender],
) -> None:
    test_client, _spy_writer, _spy_broadcaster, _spy_sender = client
    machine = _create_machine(test_client)
    first_order = _create_order(test_client, machine_id=machine["id"])
    second_order = _create_order(test_client, machine_id=machine["id"], user_id="user-2", quoted_amount_cents=500)
    first_payment = _create_payment_intent(test_client, order_id=first_order["id"], amount_cents=500)
    second_payment = _create_payment_intent(test_client, order_id=second_order["id"], amount_cents=500)

    shared_tx_hash = "0xsharedtxhash"
    first_payload = _webhook_payload(
        first_payment,
        status="payment-successful",
        currency="USDC",
        tx_signature=shared_tx_hash,
        request_id="evt_first_tx",
    )
    first_body, first_headers = _sign_payload(first_payload)
    first_response = test_client.post("/api/v1/payments/hsp/webhooks", content=first_body, headers=first_headers)
    assert first_response.status_code == 200

    second_payload = _webhook_payload(
        second_payment,
        status="payment-successful",
        currency="USDC",
        tx_signature=shared_tx_hash,
        request_id="evt_second_tx",
    )
    second_body, second_headers = _sign_payload(second_payload)
    second_response = test_client.post("/api/v1/payments/hsp/webhooks", content=second_body, headers=second_headers)
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "HSP tx signature already used by another payment"
