import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.routes import payments as payments_module
from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.models import Payment
from app.integrations.hsp_adapter import HSPMerchantOrder, HSPWebhookEvent
from app.integrations.onchain_broadcaster import OnchainCreateOrderReceipt, get_onchain_broadcaster
from app.main import create_app
from app.onchain.order_writer import OrderWriteResult, get_order_writer
from app.onchain.receipts import ChainReceipt
from app.onchain.tx_sender import get_onchain_transaction_sender


def _receipt_for_transfer(*, tx_hash: str, token_address: str, pay_to_address: str, amount_cents: int) -> ChainReceipt:
    return ChainReceipt(
        tx_hash=tx_hash,
        status=1,
        from_address="0x1111111111111111111111111111111111111111",
        to_address=token_address,
        block_number=12345,
        event_id=f"receipt:{tx_hash}:12345",
        metadata={
            "logs": [
                {
                    "address": token_address,
                    "topics": [
                        payments_module.ERC20_TRANSFER_TOPIC,
                        "0x" + "0" * 24 + "1111111111111111111111111111111111111111",
                        "0x" + "0" * 24 + pay_to_address.removeprefix("0x"),
                    ],
                    "data": hex(amount_cents * 10_000),
                }
            ]
        },
    )


class SpyOrderWriter:
    def __init__(self) -> None:
        self.create_order_calls = []
        self.pay_order_by_adapter_calls = []

    def create_order(self, order, *, buyer_wallet_address, gross_amount_override=None):
        gross_amount = order.quoted_amount_cents if gross_amount_override is None else gross_amount_override
        self.create_order_calls.append(
            {"order_id": order.id, "buyer_wallet_address": buyer_wallet_address, "gross_amount": gross_amount}
        )
        return OrderWriteResult(
            tx_hash="0xcreateorder",
            submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            chain_id=133,
            contract_name="OrderPaymentRouter",
            contract_address="0x0000000000000000000000000000000000000134",
            method_name="createOrderByAdapter",
            idempotency_key="writer-create-order",
            payload={"buyer": buyer_wallet_address, "machine_id": order.machine_id, "gross_amount": gross_amount},
        )

    def pay_order_by_adapter(self, order, payment):
        self.pay_order_by_adapter_calls.append(
            {"order_id": order.id, "payment_id": payment.id, "amount": payment.amount_cents * 10_000}
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
                "amount": payment.amount_cents * 10_000,
                "payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
            },
        )

    def mark_order_paid(self, order, payment):
        return None


class SpyOnchainBroadcaster:
    def broadcast_create_order(self, *, write_result):
        return OnchainCreateOrderReceipt(
            onchain_order_id="97001",
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:97001:{write_result.tx_hash}",
            block_number=3330001,
        )

    def broadcast_create_paid_order(self, *, write_result):
        return OnchainCreateOrderReceipt(
            onchain_order_id="97001",
            tx_hash=write_result.tx_hash,
            event_id=f"OrderCreated:97001:{write_result.tx_hash}",
            block_number=3330002,
        )


class SpyTransactionSender:
    def send(self, write_result):
        return write_result


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOrderWriter]:
    db_path = tmp_path / "hsp-polling.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = json.dumps({"user-2": "0x2222222222222222222222222222222222222222"})
    os.environ["OUTCOMEX_HSP_APP_KEY"] = "ak_test"
    os.environ["OUTCOMEX_HSP_APP_SECRET"] = "as_test_secret"
    os.environ["OUTCOMEX_HSP_PAY_TO_ADDRESS"] = "0x9999999999999999999999999999999999999999"
    os.environ["OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM"] = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIEf8gQYenT5tskecihwTBGvrfqSTA3hRrunNTOADm/jJoAcGBSuBBAAK\noUQDQgAEOas7ZFkne5CsJx2VH70raQ4h9vSAmPe3Gtw2WKoz4yicVfBrPcc2LQHt\nBKXyZPxdDRrU0XLRNQJZxluyoE0Vaw==\n-----END EC PRIVATE KEY-----"
    os.environ["OUTCOMEX_HSP_SUPPORTED_CURRENCIES"] = "USDC,USDT"
    os.environ["OUTCOMEX_ONCHAIN_USDC_ADDRESS"] = "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e"
    os.environ["OUTCOMEX_ONCHAIN_USDT_ADDRESS"] = "0x372325443233fEbaC1F6998aC750276468c83CC6"
    reset_settings_cache()
    reset_container_cache()
    spy_writer = SpyOrderWriter()
    app = create_app()
    app.dependency_overrides[get_order_writer] = lambda: spy_writer
    app.dependency_overrides[get_onchain_broadcaster] = lambda: SpyOnchainBroadcaster()
    app.dependency_overrides[get_onchain_transaction_sender] = lambda: SpyTransactionSender()
    container = get_container()
    container.hsp_adapter.create_payment_intent = lambda **_: HSPMerchantOrder(
        provider="hsp",
        merchant_order_id="order-merchant-1",
        flow_id="flow-1",
        provider_reference="PAY-REQ-1",
        payment_url="https://pay.hashkey.com/flow/flow-1",
        amount_cents=500,
        currency="USDC",
        provider_payload={"mode": "live"},
    )
    container.hsp_adapter.query_payment_status = lambda **_: HSPWebhookEvent(
        event_id="req_1",
        payment_request_id="PAY-REQ-1",
        cart_mandate_id="order-merchant-1",
        flow_id="flow-1",
        status="payment-successful",
        amount_cents=500,
        currency="USDC",
        tx_hash="0x" + "1" * 64,
    )
    with TestClient(app) as test_client:
        yield test_client, spy_writer
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post("/api/v1/machines", json={"display_name": "GANA node", "owner_user_id": "owner-1"})
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "machine_id": machine_id,
            "user_id": "user-2",
            "chat_session_id": "chat-1",
            "user_prompt": "Need an investor summary",
            "input_files": [],
            "quoted_amount_cents": 500,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_order_detail_includes_latest_hsp_payment_summary(client) -> None:
    test_client, _ = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "USDC"},
    )
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")

    assert order_after.status_code == 200
    payload = order_after.json()
    assert payload["latest_payment"] == {
        "payment_id": payment["payment_id"],
        "provider": "hsp",
        "provider_reference": "PAY-REQ-1",
        "merchant_order_id": "order-merchant-1",
        "checkout_url": "https://pay.hashkey.com/flow/flow-1",
        "state": "pending",
        "callback_state": None,
        "callback_event_id": None,
        "callback_tx_hash": None,
        "amount_cents": 500,
        "currency": "USDC",
        "created_at": payload["latest_payment"]["created_at"],
    }


def test_sync_hsp_payment_endpoint_marks_payment_succeeded(client, monkeypatch) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "USDC"},
    )
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()

    monkeypatch.setattr(
        payments_module,
        "get_receipt_reader",
        lambda: type(
            "ReceiptReaderStub",
            (),
            {
                "get_receipt": staticmethod(
                    lambda tx_hash: _receipt_for_transfer(
                        tx_hash=tx_hash,
                        token_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                        pay_to_address="0x9999999999999999999999999999999999999999",
                        amount_cents=500,
                    )
                )
            },
        )(),
    )

    sync_response = test_client.post(f"/api/v1/payments/{payment['payment_id']}/sync-hsp")

    assert sync_response.status_code == 200
    payload = sync_response.json()
    assert payload["state"] == "succeeded"
    assert payload["remote_status"] == "payment-successful"
    assert payload["polled"] is True
    assert len(spy_writer.pay_order_by_adapter_calls) == 1
    assert spy_writer.create_order_calls == [
        {
            "order_id": order["id"],
            "buyer_wallet_address": "0x2222222222222222222222222222222222222222",
            "gross_amount": 500,
        }
    ]

    order_after = test_client.get(f"/api/v1/orders/{order['id']}")
    assert order_after.status_code == 200
    assert order_after.json()["payment_state"] == "succeeded"

    container = get_container()
    with container.session_factory() as db:
        payment_row = db.get(Payment, payment["payment_id"])
        assert payment_row is not None
        assert payment_row.callback_event_id == "req_1"
        assert payment_row.callback_tx_hash == "0x" + "1" * 64
    assert spy_writer.pay_order_by_adapter_calls == [
        {
            "order_id": order["id"],
            "payment_id": payment["payment_id"],
            "amount": 500 * 10_000,
        }
    ]


def test_sync_hsp_payment_promotes_included_status_when_receipt_confirms_transfer(client, monkeypatch) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "USDC"},
    )
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()

    container = get_container()
    container.hsp_adapter.query_payment_status = lambda **_: HSPWebhookEvent(
        event_id="req_included",
        payment_request_id="PAY-REQ-1",
        cart_mandate_id="order-merchant-1",
        flow_id="flow-1",
        status="payment-included",
        amount_cents=500,
        currency="USDC",
        tx_hash="0x" + "2" * 64,
    )

    class ReceiptReaderStub:
        def get_receipt(self, tx_hash: str):
            assert tx_hash == "0x" + "2" * 64
            return ChainReceipt(
                tx_hash=tx_hash,
                status=1,
                from_address="0x1111111111111111111111111111111111111111",
                to_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                block_number=12345,
                event_id=f"receipt:{tx_hash}:12345",
                metadata={
                    "logs": [
                        {
                            "address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                            "topics": [
                                payments_module.ERC20_TRANSFER_TOPIC,
                                "0x" + "0" * 24 + "1111111111111111111111111111111111111111",
                                "0x" + "0" * 24 + "9999999999999999999999999999999999999999",
                            ],
                            "data": hex(500 * 10_000),
                        }
                    ]
                },
            )

    monkeypatch.setattr(payments_module, "get_receipt_reader", lambda: ReceiptReaderStub())

    sync_response = test_client.post(f"/api/v1/payments/{payment['payment_id']}/sync-hsp")

    assert sync_response.status_code == 200
    payload = sync_response.json()
    assert payload["state"] == "succeeded"
    assert payload["remote_status"] == "payment-included"
    assert payload["polled"] is True
    assert len(spy_writer.pay_order_by_adapter_calls) == 1


def test_sync_hsp_payment_accepts_payment_finalized_status(client, monkeypatch) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "USDC"},
    )
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()

    container = get_container()
    container.hsp_adapter.query_payment_status = lambda **_: HSPWebhookEvent(
        event_id="req_finalized",
        payment_request_id="PAY-REQ-FINALIZED",
        cart_mandate_id="order-merchant-finalized",
        flow_id="flow-finalized",
        status="payment-finalized",
        amount_cents=500,
        currency="USDC",
        tx_hash="0x" + "3" * 64,
    )
    monkeypatch.setattr(
        payments_module,
        "get_receipt_reader",
        lambda: type(
            "ReceiptReaderStub",
            (),
            {
                "get_receipt": staticmethod(
                    lambda tx_hash: _receipt_for_transfer(
                        tx_hash=tx_hash,
                        token_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                        pay_to_address="0x9999999999999999999999999999999999999999",
                        amount_cents=500,
                    )
                )
            },
        )(),
    )

    sync_response = test_client.post(f"/api/v1/payments/{payment['payment_id']}/sync-hsp")

    assert sync_response.status_code == 200
    payload = sync_response.json()
    assert payload["state"] == "succeeded"
    assert payload["remote_status"] == "payment-finalized"
    assert payload["polled"] is True
    assert len(spy_writer.pay_order_by_adapter_calls) == 1


def test_sync_hsp_payment_rejects_success_without_pay_to_transfer(client, monkeypatch) -> None:
    test_client, spy_writer = client
    machine = _create_machine(test_client)
    order = _create_order(test_client, machine_id=machine["id"])
    payment_response = test_client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 500, "currency": "USDC"},
    )
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()

    monkeypatch.setattr(
        payments_module,
        "get_receipt_reader",
        lambda: type(
            "ReceiptReaderStub",
            (),
            {
                "get_receipt": staticmethod(
                    lambda tx_hash: _receipt_for_transfer(
                        tx_hash=tx_hash,
                        token_address="0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
                        pay_to_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        amount_cents=500,
                    )
                )
            },
        )(),
    )

    sync_response = test_client.post(f"/api/v1/payments/{payment['payment_id']}/sync-hsp")

    assert sync_response.status_code == 409
    assert sync_response.json()["detail"] == "HSP receipt verification failed"
    assert spy_writer.pay_order_by_adapter_calls == []
