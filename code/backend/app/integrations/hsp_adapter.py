from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json


def _stable_identifier(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class HSPMerchantOrder:
    provider: str
    merchant_order_id: str
    flow_id: str
    provider_reference: str
    payment_url: str
    amount_cents: int
    currency: str


@dataclass(slots=True)
class HSPWebhookEvent:
    event_id: str
    merchant_order_id: str
    flow_id: str
    status: str
    amount_cents: int
    currency: str
    tx_hash: str | None = None


class HSPAdapter:
    """Deterministic merchant-order boundary shaped like the real HSP integration."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def create_merchant_order(self, *, order_id: str, amount_cents: int, currency: str) -> HSPMerchantOrder:
        normalized_currency = currency.upper()
        merchant_order_id = _stable_identifier("merchant", order_id, str(amount_cents), normalized_currency)
        flow_id = _stable_identifier("flow", merchant_order_id, order_id)
        return HSPMerchantOrder(
            provider="hsp",
            merchant_order_id=merchant_order_id,
            flow_id=flow_id,
            provider_reference=flow_id,
            payment_url=f"{self.base_url}/checkout/{flow_id}?merchant_order_id={merchant_order_id}",
            amount_cents=amount_cents,
            currency=normalized_currency,
        )

    def create_payment_intent(self, order_id: str, amount_cents: int, currency: str) -> HSPMerchantOrder:
        return self.create_merchant_order(order_id=order_id, amount_cents=amount_cents, currency=currency)

    def parse_webhook(self, body: bytes) -> HSPWebhookEvent:
        payload = json.loads(body.decode("utf-8"))
        return HSPWebhookEvent(
            event_id=str(payload["event_id"]),
            merchant_order_id=str(payload["merchant_order_id"]),
            flow_id=str(payload["flow_id"]),
            status=str(payload["status"]).lower(),
            amount_cents=int(payload["amount_cents"]),
            currency=str(payload["currency"]).upper(),
            tx_hash=str(payload["tx_hash"]) if payload.get("tx_hash") else None,
        )

    def build_webhook_signature(self, *, body: bytes, timestamp: str) -> str:
        signed_payload = f"{timestamp}.".encode("utf-8") + body
        return hmac.new(
            self.api_key.encode("utf-8"),
            msg=signed_payload,
            digestmod=hashlib.sha256,
        ).hexdigest()

    def verify_webhook_signature(self, *, body: bytes, signature: str | None, timestamp: str | None) -> bool:
        if not signature or not timestamp:
            return False
        expected_signature = self.build_webhook_signature(body=body, timestamp=timestamp)
        return hmac.compare_digest(expected_signature, signature)
