import hashlib
import hmac
import json
import os
import time
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

# Keep this module hermetic even when the shell has live HSP / RPC credentials set.
os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
os.environ["OUTCOMEX_HSP_APP_KEY"] = ""
os.environ["OUTCOMEX_HSP_APP_SECRET"] = ""
os.environ["OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM"] = ""
os.environ["OUTCOMEX_HSP_PAY_TO_ADDRESS"] = ""

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import PaymentState
from app.api.routes.primary_issuance import _reserve_primary_stock_atomically
from app.domain.models import Machine, PrimaryIssuancePurchase, PrimaryIssuanceSku
from app.main import create_app
from app.onchain.lifecycle_service import MintedMachineReceipt, get_onchain_lifecycle_service


class SpyOnchainLifecycleService:
    def __init__(self) -> None:
        self.mint_calls: list[dict[str, str]] = []
        self.reconcile_hits: dict[str, str] = {}
        self.find_calls: list[str] = []
        self.find_error: str | None = None
        self._counter = 0

    def enabled(self) -> bool:
        return True

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str) -> MintedMachineReceipt:
        self._counter += 1
        machine_id = str(9000 + self._counter)
        self.mint_calls.append(
            {
                "owner_user_id": owner_user_id,
                "token_uri": token_uri,
                "onchain_machine_id": machine_id,
            }
        )
        return MintedMachineReceipt(
            tx_hash=f"0xmint{self._counter}",
            receipt=None,
            onchain_machine_id=machine_id,
        )

    def find_minted_machine_by_token_uri(self, *, token_uri: str) -> str | None:
        self.find_calls.append(token_uri)
        if self.find_error:
            raise RuntimeError(self.find_error)
        return self.reconcile_hits.get(token_uri)


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, SpyOnchainLifecycleService]:
    db_path = tmp_path / "primary-issuance.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = json.dumps(
        {
            "buyer-1": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            "owner-1": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        }
    )
    reset_settings_cache()
    reset_container_cache()

    spy_lifecycle = SpyOnchainLifecycleService()
    app = create_app()
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: spy_lifecycle
    with TestClient(app) as test_client:
        yield test_client, spy_lifecycle

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
    purchase: dict,
    *,
    status: str,
    request_id: str,
    tx_signature: str | None,
) -> dict:
    payload = {
        "event_type": "payment",
        "payment_request_id": purchase["provider_reference"],
        "request_id": request_id,
        "cart_mandate_id": purchase["merchant_order_id"],
        "amount": str(purchase["amount_cents"] * 10_000),
        "token": purchase["currency"],
        "token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        "chain": "eip155:133",
        "network": "hashkey-testnet",
        "status": status,
        "created_at": "2026-04-10T12:00:00Z",
    }
    if tx_signature:
        payload["tx_signature"] = tx_signature
    return payload


def _create_purchase_intent(client: TestClient, *, buyer_user_id: str = "buyer-1") -> dict:
    response = client.post(
        "/api/v1/primary-issuance/skus/apple-silicon-96gb-qwen-family/purchase-intent",
        json={"buyer_user_id": buyer_user_id},
    )
    assert response.status_code == 201
    return response.json()


def test_list_primary_issuance_skus_returns_fixed_catalog_with_stock(client: tuple[TestClient, SpyOnchainLifecycleService]) -> None:
    test_client, _spy_lifecycle = client

    response = test_client.get("/api/v1/primary-issuance/skus")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    sku = payload[0]
    assert sku["sku_id"] == "apple-silicon-96gb-qwen-family"
    assert sku["display_name"] == "Apple Silicon 96GB Unified Memory + Qwen Family"
    assert sku["price_cents"] == 390
    assert sku["currency"] == "USDT"
    assert sku["stock_available"] == 10


def test_create_primary_purchase_intent_persists_purchase_and_hsp_intent(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client

    payload = _create_purchase_intent(test_client)
    assert payload["sku_id"] == "apple-silicon-96gb-qwen-family"
    assert payload["buyer_user_id"] == "buyer-1"
    assert payload["state"] == PaymentState.PENDING.value
    assert payload["amount_cents"] == 390
    assert payload["currency"] == "USDT"
    assert payload["checkout_url"].startswith("https://mock-hsp.local/checkout/")
    assert payload["provider"] == "hsp"
    assert payload["provider_reference"].startswith("payreq_")

    with get_container().session_factory() as session:
        purchase = session.get(PrimaryIssuancePurchase, payload["purchase_id"])
        assert purchase is not None
        assert purchase.sku_id == "apple-silicon-96gb-qwen-family"
        assert purchase.state == PaymentState.PENDING
        assert purchase.minted_machine_id is None
        assert purchase.stock_reserved is True
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        assert sku.stock_available == 9


def test_primary_purchase_intent_rejects_out_of_stock_after_reservations(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client

    for _ in range(10):
        _create_purchase_intent(test_client)

    exhausted = test_client.post(
        "/api/v1/primary-issuance/skus/apple-silicon-96gb-qwen-family/purchase-intent",
        json={"buyer_user_id": "buyer-1"},
    )

    assert exhausted.status_code == 409
    assert exhausted.json()["detail"] == "Primary issuance stock exhausted"


def test_atomic_stock_reservation_only_consumes_last_unit_once(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client
    sku_response = test_client.get("/api/v1/primary-issuance/skus")
    assert sku_response.status_code == 200
    with get_container().session_factory() as session:
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        sku.stock_available = 1
        session.add(sku)
        session.commit()

    with get_container().session_factory() as session:
        first = _reserve_primary_stock_atomically(
            sku_id="apple-silicon-96gb-qwen-family",
            db=session,
        )
        second = _reserve_primary_stock_atomically(
            sku_id="apple-silicon-96gb-qwen-family",
            db=session,
        )
        session.commit()
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        assert sku.stock_available == 0

    assert first is True
    assert second is False


def test_list_primary_issuance_skus_rewrites_existing_catalog_currency_to_usdt(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client

    test_client.get("/api/v1/primary-issuance/skus")
    with get_container().session_factory() as session:
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        sku.currency = "USDC"
        session.add(sku)
        session.commit()

    response = test_client.get("/api/v1/primary-issuance/skus")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["currency"] == "USDT"
    with get_container().session_factory() as session:
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        assert sku.currency == "USDT"


def test_successful_hsp_webhook_finalizes_primary_purchase_exactly_once(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)

    payload = _webhook_payload(
        purchase,
        status="payment-successful",
        request_id="evt-primary-1",
        tx_signature="0xabc123",
    )
    body, headers = _sign_payload(payload)

    first = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json() == {
        "purchase_id": purchase["purchase_id"],
        "state": PaymentState.SUCCEEDED.value,
        "duplicate": False,
        "minted_machine_id": ANY,
        "minted_onchain_machine_id": "9001",
    }

    duplicate = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "purchase_id": purchase["purchase_id"],
        "state": PaymentState.SUCCEEDED.value,
        "duplicate": True,
        "minted_machine_id": ANY,
        "minted_onchain_machine_id": "9001",
    }

    skus = test_client.get("/api/v1/primary-issuance/skus")
    assert skus.status_code == 200
    assert skus.json()[0]["stock_available"] == 9

    with get_container().session_factory() as session:
        persisted_purchase = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted_purchase is not None
        assert persisted_purchase.state == PaymentState.SUCCEEDED
        assert persisted_purchase.minted_machine_id is not None
        assert persisted_purchase.minted_onchain_machine_id == "9001"

        minted_machine = session.get(Machine, persisted_purchase.minted_machine_id)
        assert minted_machine is not None
        assert minted_machine.owner_user_id == "buyer-1"
        assert minted_machine.onchain_machine_id == "9001"

    assert len(spy_lifecycle.mint_calls) == 1


def test_failed_primary_hsp_webhook_releases_reserved_stock_exactly_once(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)
    assert test_client.get("/api/v1/primary-issuance/skus").json()[0]["stock_available"] == 9

    fail_payload = _webhook_payload(
        purchase,
        status="payment-failed",
        request_id="evt-primary-failed-1",
        tx_signature=None,
    )
    body, headers = _sign_payload(fail_payload)

    first = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["state"] == PaymentState.FAILED.value
    assert first.json()["duplicate"] is False

    duplicate = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["state"] == PaymentState.FAILED.value
    assert duplicate.json()["duplicate"] is True

    assert test_client.get("/api/v1/primary-issuance/skus").json()[0]["stock_available"] == 10


def test_primary_success_webhook_requires_tx_signature(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)
    payload = _webhook_payload(
        purchase,
        status="payment-successful",
        request_id="evt-primary-no-tx",
        tx_signature=None,
    )
    body, headers = _sign_payload(payload)

    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Successful primary webhook must include tx signature"


def test_primary_success_webhook_rejects_reused_tx_hash(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client
    first_purchase = _create_purchase_intent(test_client)
    second_purchase = _create_purchase_intent(test_client)

    first_payload = _webhook_payload(
        first_purchase,
        status="payment-successful",
        request_id="evt-primary-tx-1",
        tx_signature="0xreusedtx",
    )
    first_body, first_headers = _sign_payload(first_payload)
    first = test_client.post("/api/v1/payments/hsp/webhooks", content=first_body, headers=first_headers)
    assert first.status_code == 200

    second_payload = _webhook_payload(
        second_purchase,
        status="payment-successful",
        request_id="evt-primary-tx-2",
        tx_signature="0xreusedtx",
    )
    second_body, second_headers = _sign_payload(second_payload)
    second = test_client.post("/api/v1/payments/hsp/webhooks", content=second_body, headers=second_headers)

    assert second.status_code == 409
    assert second.json()["detail"] == "Primary tx signature already used by another purchase"


def test_primary_success_retry_with_new_event_does_not_remint_after_success_marker(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)
    token_uri = f"ipfs://outcomex-machine/primary-issuance/{purchase['purchase_id']}"
    spy_lifecycle.reconcile_hits[token_uri] = "9901"

    with get_container().session_factory() as session:
        persisted = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted is not None
        persisted.state = PaymentState.SUCCEEDED
        persisted.callback_event_id = "evt-initial-success"
        persisted.stock_reserved = True
        session.add(persisted)
        session.commit()

    retry_payload = _webhook_payload(
        purchase,
        status="payment-successful",
        request_id="evt-primary-retry-success",
        tx_signature="0xretrynewtx",
    )
    retry_body, retry_headers = _sign_payload(retry_payload)
    retry = test_client.post("/api/v1/payments/hsp/webhooks", content=retry_body, headers=retry_headers)

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False
    assert retry.json()["minted_onchain_machine_id"] == "9901"
    assert len(spy_lifecycle.mint_calls) == 0
    assert spy_lifecycle.find_calls == [token_uri]

    with get_container().session_factory() as session:
        persisted = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted is not None
        assert persisted.minted_machine_id is not None
        assert persisted.minted_onchain_machine_id == "9901"
        assert persisted.stock_reserved is False


def test_primary_fresh_success_skips_reconciliation_and_mints_directly(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)
    spy_lifecycle.find_error = "machine_minted_log_fetch_failed"

    payload = _webhook_payload(
        purchase,
        status="payment-successful",
        request_id="evt-primary-fresh-success",
        tx_signature="0xfreshsuccess",
    )
    body, headers = _sign_payload(payload)
    response = test_client.post("/api/v1/payments/hsp/webhooks", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert response.json()["minted_onchain_machine_id"] == "9001"
    assert spy_lifecycle.find_calls == []
    assert len(spy_lifecycle.mint_calls) == 1

    with get_container().session_factory() as session:
        persisted = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted is not None
        assert persisted.state == PaymentState.SUCCEEDED
        assert persisted.minted_onchain_machine_id == "9001"
        assert persisted.stock_reserved is False


def test_primary_reconciliation_uncertainty_fails_closed_without_remint(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, spy_lifecycle = client
    purchase = _create_purchase_intent(test_client)
    spy_lifecycle.find_error = "machine_minted_log_fetch_failed"

    with get_container().session_factory() as session:
        persisted = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted is not None
        persisted.state = PaymentState.SUCCEEDED
        persisted.callback_event_id = "evt-initial-success"
        persisted.stock_reserved = True
        session.add(persisted)
        session.commit()

    retry_payload = _webhook_payload(
        purchase,
        status="payment-successful",
        request_id="evt-primary-retry-fail-closed",
        tx_signature="0xretryclosed",
    )
    retry_body, retry_headers = _sign_payload(retry_payload)
    retry = test_client.post("/api/v1/payments/hsp/webhooks", content=retry_body, headers=retry_headers)

    assert retry.status_code == 409
    assert retry.json()["detail"] == "Primary issuance reconciliation unavailable: machine_minted_log_fetch_failed"
    assert len(spy_lifecycle.mint_calls) == 0

    with get_container().session_factory() as session:
        persisted = session.get(PrimaryIssuancePurchase, purchase["purchase_id"])
        assert persisted is not None
        assert persisted.minted_machine_id is None
        assert persisted.stock_reserved is True
