import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.integrations.hsp_adapter import HSPAdapter


def _private_key_pem() -> str:
    private_key = ec.generate_private_key(ec.SECP256K1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def test_hsp_adapter_live_create_order_uses_hashkey_hmac_and_returns_checkout() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "payment_request_id": "PAY-REQ-1",
                    "payment_url": "https://pay.hashkey.com/flow/flow-123",
                    "multi_pay": False,
                },
            },
        )

    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        redirect_url="https://app.outcomex.ai/payment/callback",
        usdc_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    merchant_order = adapter.create_payment_intent(
        order_id="order-123",
        amount_cents=500,
        currency="USDC",
    )

    headers = captured["headers"]
    body = json.loads(captured["body"])
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/merchant/orders"
    assert headers["x-app-key"] == "ak_test"
    assert headers["x-timestamp"]
    assert headers["x-nonce"]
    assert "merchant_authorization" in body["cart_mandate"]
    assert body["cart_mandate"]["contents"]["payment_request"]["method_data"][0]["data"]["coin"] == "USDC"
    assert body["cart_mandate"]["contents"]["payment_request"]["method_data"][0]["data"]["contract_address"] == "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"
    assert body["cart_mandate"]["contents"]["payment_request"]["method_data"][0]["data"]["pay_to"] == "0x9999999999999999999999999999999999999999"
    assert body["redirect_url"] == "https://app.outcomex.ai/payment/callback"

    body_hash = hashlib.sha256(captured["body"].encode("utf-8")).hexdigest()
    expected_message = "\n".join(
        [
            "POST",
            "/api/v1/merchant/orders",
            "",
            body_hash,
            headers["x-timestamp"],
            headers["x-nonce"],
        ]
    )
    expected_signature = hmac.new(b"as_test_secret", expected_message.encode("utf-8"), hashlib.sha256).hexdigest()
    assert headers["x-signature"] == expected_signature

    assert merchant_order.provider_reference == "PAY-REQ-1"
    assert merchant_order.payment_url == "https://pay.hashkey.com/flow/flow-123"
    assert merchant_order.flow_id == "flow-123"
    assert merchant_order.merchant_order_id == "order-123"


def test_hsp_adapter_defaults_cart_expiry_into_the_future_when_missing() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "payment_request_id": "PAY-REQ-2",
                    "payment_url": "https://pay.hashkey.com/flow/flow-456",
                    "multi_pay": False,
                },
            },
        )

    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        redirect_url="https://app.outcomex.ai/payment/callback",
        usdc_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    before = datetime.now(timezone.utc) + timedelta(minutes=119)
    adapter.create_payment_intent(
        order_id="order-456",
        amount_cents=500,
        currency="USDC",
        expires_at=None,
    )
    body = json.loads(captured["body"])
    expiry = datetime.fromisoformat(body["cart_mandate"]["contents"]["cart_expiry"].replace("Z", "+00:00"))
    after = datetime.now(timezone.utc) + timedelta(minutes=121)
    assert before <= expiry <= after


def test_hsp_adapter_can_run_live_mode_with_only_usdt_enabled() -> None:
    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        supported_currencies="USDT",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "code": 0,
            "msg": "success",
            "data": {
                "payment_request_id": "PAY-REQ-USDT",
                "payment_url": "https://pay.hashkey.com/flow/flow-usdt",
                "multi_pay": False,
            },
        }))),
    )

    assert adapter.is_live_configured is True
    assert adapter.supports_currency("USDT") is True
    assert adapter.supports_currency("USDC") is False
    merchant_order = adapter.create_payment_intent(order_id="order-usdt", amount_cents=500, currency="USDT")
    assert merchant_order.provider_reference == "PAY-REQ-USDT"


def test_hsp_adapter_queries_payment_status_with_hmac_get() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode("utf-8")
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "payment_request_id": "PAY-REQ-9",
                    "request_id": "req_9",
                    "cart_mandate_id": "order-789",
                    "flow_id": "flow-789",
                    "amount": "5000000",
                    "token": "USDC",
                    "status": "payment-successful",
                    "tx_signature": "0xabc",
                },
            },
        )

    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        redirect_url="https://app.outcomex.ai/payment/callback",
        usdc_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status_payload = adapter.query_payment_status(payment_request_id="PAY-REQ-9")

    headers = captured["headers"]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/merchant/payments"
    assert captured["query"] == "payment_request_id=PAY-REQ-9"
    expected_message = "\n".join(
        [
            "GET",
            "/api/v1/merchant/payments",
            "payment_request_id=PAY-REQ-9",
            "",
            headers["x-timestamp"],
            headers["x-nonce"],
        ]
    )
    expected_signature = hmac.new(b"as_test_secret", expected_message.encode("utf-8"), hashlib.sha256).hexdigest()
    assert headers["x-signature"] == expected_signature
    assert status_payload is not None
    assert status_payload.payment_request_id == "PAY-REQ-9"
    assert status_payload.status == "payment-successful"
    assert status_payload.tx_hash == "0xabc"


def test_hsp_adapter_query_payment_status_handles_payment_required_payloads_without_amounts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "payment_request_id": "PAY-REQ-10",
                    "request_id": "",
                    "flow_id": "flow-10",
                    "status": "payment-required",
                    "amount": "",
                    "token": "",
                },
            },
        )

    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        supported_currencies="USDT",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status_payload = adapter.query_payment_status(
        payment_request_id="PAY-REQ-10",
        fallback_amount_cents=100,
        fallback_currency="USDT",
    )

    assert status_payload is not None
    assert status_payload.payment_request_id == "PAY-REQ-10"
    assert status_payload.cart_mandate_id == ""
    assert status_payload.status == "payment-required"
    assert status_payload.amount_cents == 100
    assert status_payload.currency == "USDT"


def test_hsp_adapter_query_payment_status_accepts_decimal_amounts_from_merchant_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "payment_request_id": "PAY-REQ-11",
                    "request_id": "req_11",
                    "cart_mandate_id": "order-11",
                    "flow_id": "flow-11",
                    "amount": "1",
                    "token": "USDT",
                    "status": "payment-submitted",
                },
            },
        )

    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        api_base_url="https://merchant-qa.hashkeymerchant.com",
        app_key="ak_test",
        app_secret="as_test_secret",
        merchant_name="OutcomeX",
        merchant_private_key_pem=_private_key_pem(),
        network="hashkey-testnet",
        chain_id=133,
        pay_to_address="0x9999999999999999999999999999999999999999",
        supported_currencies="USDT",
        usdt_address="0x372325443233fEbaC1F6998aC750276468c83CC6",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status_payload = adapter.query_payment_status(
        payment_request_id="PAY-REQ-11",
        fallback_amount_cents=100,
        fallback_currency="USDT",
    )

    assert status_payload is not None
    assert status_payload.status == "payment-submitted"
    assert status_payload.amount_cents == 100
    assert status_payload.currency == "USDT"


def test_hsp_adapter_verifies_hashkey_webhook_signature_header() -> None:
    adapter = HSPAdapter(
        base_url="https://mock-hsp.local",
        api_key="legacy-dev-key",
        app_secret="as_test_secret",
        webhook_tolerance_seconds=300,
    )
    payload = {
        "event_type": "payment",
        "payment_request_id": "PAY-REQ-1",
        "request_id": "req-1",
        "cart_mandate_id": "order-123",
        "payer_address": "0x1111111111111111111111111111111111111111",
        "amount": "5000000",
        "token": "USDC",
        "token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        "chain": "eip155:133",
        "network": "hashkey-testnet",
        "status": "payment-successful",
        "created_at": "2026-04-08T12:00:00Z",
        "tx_signature": "0xabc",
        "completed_at": "2026-04-08T12:00:30Z",
    }
    body = json.dumps(payload).encode("utf-8")
    timestamp = "1712233445"
    digest = hmac.new(b"as_test_secret", f"{timestamp}.".encode("utf-8") + body, hashlib.sha256).hexdigest()

    assert adapter.verify_webhook_signature(
        body=body,
        signature_header=f"t={timestamp},v1={digest}",
        now_ts=1712233445,
    )
    assert not adapter.verify_webhook_signature(
        body=body,
        signature_header=f"t={timestamp},v1=deadbeef",
        now_ts=1712233445,
    )
