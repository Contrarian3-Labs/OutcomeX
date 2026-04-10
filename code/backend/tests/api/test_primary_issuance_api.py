import hashlib
import hmac
import json
import os
import time
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import PaymentState
from app.domain.models import Machine, PrimaryIssuancePurchase, PrimaryIssuanceSku
from app.main import create_app
from app.onchain.lifecycle_service import MintedMachineReceipt, get_onchain_lifecycle_service


class SpyOnchainLifecycleService:
    def __init__(self) -> None:
        self.mint_calls: list[dict[str, str]] = []
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
    assert sku["currency"] == "USDC"
    assert sku["stock_available"] == 10


def test_create_primary_purchase_intent_persists_purchase_and_hsp_intent(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, _spy_lifecycle = client

    response = test_client.post(
        "/api/v1/primary-issuance/skus/apple-silicon-96gb-qwen-family/purchase-intent",
        json={"buyer_user_id": "buyer-1"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["sku_id"] == "apple-silicon-96gb-qwen-family"
    assert payload["buyer_user_id"] == "buyer-1"
    assert payload["state"] == PaymentState.PENDING.value
    assert payload["amount_cents"] == 390
    assert payload["currency"] == "USDC"
    assert payload["checkout_url"].startswith("https://mock-hsp.local/checkout/")
    assert payload["provider"] == "hsp"
    assert payload["provider_reference"].startswith("payreq_")

    with get_container().session_factory() as session:
        purchase = session.get(PrimaryIssuancePurchase, payload["purchase_id"])
        assert purchase is not None
        assert purchase.sku_id == "apple-silicon-96gb-qwen-family"
        assert purchase.state == PaymentState.PENDING
        assert purchase.minted_machine_id is None
        sku = session.get(PrimaryIssuanceSku, "apple-silicon-96gb-qwen-family")
        assert sku is not None
        assert sku.stock_available == 10


def test_successful_hsp_webhook_finalizes_primary_purchase_exactly_once(
    client: tuple[TestClient, SpyOnchainLifecycleService],
) -> None:
    test_client, spy_lifecycle = client
    create_response = test_client.post(
        "/api/v1/primary-issuance/skus/apple-silicon-96gb-qwen-family/purchase-intent",
        json={"buyer_user_id": "buyer-1"},
    )
    assert create_response.status_code == 201
    purchase = create_response.json()

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
