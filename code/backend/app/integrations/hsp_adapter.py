from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def _stable_identifier(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _smallest_units_from_cents(amount_cents: int) -> str:
    return str(amount_cents * 10_000)


def _cents_from_smallest_units(amount: str) -> int:
    amount_int = int(amount)
    if amount_int % 10_000 != 0:
        raise ValueError("Webhook amount is not aligned to whole USD cents")
    return amount_int // 10_000


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


@dataclass(slots=True)
class HSPMerchantOrder:
    provider: str
    merchant_order_id: str
    flow_id: str | None
    provider_reference: str
    payment_url: str
    amount_cents: int
    currency: str
    provider_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class HSPWebhookEvent:
    event_id: str
    payment_request_id: str
    cart_mandate_id: str
    flow_id: str | None
    status: str
    amount_cents: int
    currency: str
    tx_hash: str | None = None


class HSPAdapter:
    """Merchant gateway boundary for HashKey Merchant orders and webhooks."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        api_base_url: str = "https://merchant-qa.hashkeymerchant.com",
        app_key: str = "",
        app_secret: str = "",
        merchant_name: str = "OutcomeX",
        merchant_private_key_pem: str = "",
        network: str = "hashkey-testnet",
        chain_id: int = 133,
        pay_to_address: str = "",
        redirect_url: str = "",
        webhook_tolerance_seconds: int = 300,
        usdc_address: str = "",
        usdt_address: str = "",
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.webhook_secret = app_secret or api_key
        self.merchant_name = merchant_name
        self.merchant_private_key_pem = merchant_private_key_pem
        self.network = network
        self.chain_id = chain_id
        self.pay_to_address = pay_to_address
        self.redirect_url = redirect_url
        self.webhook_tolerance_seconds = webhook_tolerance_seconds
        self.usdc_address = usdc_address
        self.usdt_address = usdt_address
        self._client = client or httpx.Client(timeout=30.0)

    @property
    def is_live_configured(self) -> bool:
        return bool(
            self.app_key
            and self.app_secret
            and self.merchant_private_key_pem
            and self.pay_to_address
            and self._token_address_for_currency("USDC")
            and self._token_address_for_currency("USDT")
        )

    @property
    def has_partial_live_configuration(self) -> bool:
        return bool(
            self.app_key
            or self.app_secret
            or self.merchant_private_key_pem
            or self.pay_to_address
        )

    def create_payment_intent(
        self,
        order_id: str,
        amount_cents: int,
        currency: str,
        *,
        expires_at: datetime | None = None,
    ) -> HSPMerchantOrder:
        if self.has_partial_live_configuration and not self.is_live_configured:
            raise RuntimeError(
                "Incomplete HashKey Merchant configuration: app_key, app_secret, merchant_private_key_pem, pay_to_address, and stablecoin token addresses are required for live mode"
            )
        if not self.is_live_configured:
            return self._create_mock_payment_intent(order_id=order_id, amount_cents=amount_cents, currency=currency)
        return self._create_live_payment_intent(
            order_id=order_id,
            amount_cents=amount_cents,
            currency=currency,
            expires_at=expires_at,
        )

    def _create_mock_payment_intent(self, *, order_id: str, amount_cents: int, currency: str) -> HSPMerchantOrder:
        normalized_currency = currency.upper()
        merchant_order_id = order_id
        payment_request_id = _stable_identifier("payreq", order_id, str(amount_cents), normalized_currency)
        flow_id = _stable_identifier("flow", payment_request_id, order_id)
        return HSPMerchantOrder(
            provider="hsp",
            merchant_order_id=merchant_order_id,
            flow_id=flow_id,
            provider_reference=payment_request_id,
            payment_url=f"{self.base_url}/checkout/{flow_id}?cart_mandate_id={merchant_order_id}",
            amount_cents=amount_cents,
            currency=normalized_currency,
            provider_payload={"mode": "mock"},
        )

    def _create_live_payment_intent(
        self,
        *,
        order_id: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime | None,
    ) -> HSPMerchantOrder:
        normalized_currency = currency.upper()
        token_address = self._token_address_for_currency(normalized_currency)
        payment_request_id = _stable_identifier("payreq", order_id, str(amount_cents), normalized_currency)
        payload = self._build_create_order_payload(
            order_id=order_id,
            payment_request_id=payment_request_id,
            amount_cents=amount_cents,
            currency=normalized_currency,
            token_address=token_address,
            expires_at=expires_at,
        )
        response_payload = self._merchant_request(
            method="POST",
            path="/api/v1/merchant/orders",
            json_body=payload,
        )
        data = response_payload.get("data", {})
        payment_url = str(data.get("payment_url", "")).strip()
        provider_reference = str(data.get("payment_request_id", payment_request_id)).strip()
        if not payment_url:
            raise RuntimeError("HashKey Merchant response missing payment_url")
        if not provider_reference:
            raise RuntimeError("HashKey Merchant response missing payment_request_id")
        return HSPMerchantOrder(
            provider="hsp",
            merchant_order_id=order_id,
            flow_id=self._extract_flow_id(payment_url),
            provider_reference=provider_reference,
            payment_url=payment_url,
            amount_cents=amount_cents,
            currency=normalized_currency,
            provider_payload={
                "mode": "live",
                "request_payload": payload,
                "response_payload": response_payload,
            },
        )

    def _build_create_order_payload(
        self,
        *,
        order_id: str,
        payment_request_id: str,
        amount_cents: int,
        currency: str,
        token_address: str,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        amount_value = f"{amount_cents / 100:.2f}"
        cart_expiry = (expires_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        cart_contents: dict[str, Any] = {
            "id": order_id,
            "user_cart_confirmation_required": True,
            "payment_request": {
                "method_data": [
                    {
                        "supported_methods": "https://www.x402.org/",
                        "data": {
                            "x402Version": 2,
                            "network": self.network,
                            "chain_id": self.chain_id,
                            "contract_address": token_address,
                            "pay_to": self.pay_to_address,
                            "coin": currency,
                        },
                    }
                ],
                "details": {
                    "id": payment_request_id,
                    "display_items": [
                        {
                            "label": "OutcomeX delivery",
                            "amount": {"currency": "USD", "value": amount_value},
                        }
                    ],
                    "total": {
                        "label": "Total",
                        "amount": {"currency": "USD", "value": amount_value},
                    },
                },
            },
            "cart_expiry": cart_expiry,
            "merchant_name": self.merchant_name,
        }
        merchant_authorization = self._sign_merchant_authorization(cart_contents)
        payload: dict[str, Any] = {
            "cart_mandate": {
                "contents": cart_contents,
                "merchant_authorization": merchant_authorization,
            }
        }
        if self.redirect_url:
            payload["redirect_url"] = self.redirect_url
        return payload

    def _sign_merchant_authorization(self, cart_contents: dict[str, Any]) -> str:
        header = {"alg": "ES256K", "typ": "JWT"}
        iat = _utc_timestamp()
        claims = {
            "iss": self.merchant_name,
            "sub": self.merchant_name,
            "aud": "HashkeyMerchant",
            "iat": iat,
            "exp": iat + 3600,
            "jti": f"JWT-{iat}-{secrets.token_hex(8)}",
            "cart_hash": hashlib.sha256(_canonical_json_bytes(cart_contents)).hexdigest(),
        }
        signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}.{_b64url(json.dumps(claims, separators=(',', ':')).encode('utf-8'))}"
        private_key_pem = self.merchant_private_key_pem.replace("\\n", "\n")
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        der_signature = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r_value, s_value = decode_dss_signature(der_signature)
        jose_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
        return f"{signing_input}.{_b64url(jose_signature)}"

    def _merchant_request(self, *, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        timestamp = str(_utc_timestamp())
        nonce = secrets.token_hex(16)
        canonical_body = _canonical_json_bytes(json_body) if json_body is not None else b""
        body_hash = hashlib.sha256(canonical_body).hexdigest() if canonical_body else ""
        message = "\n".join(
            [
                method.upper(),
                path,
                "",
                body_hash,
                timestamp,
                nonce,
            ]
        )
        signature = hmac.new(self.app_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-App-Key": self.app_key,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        }
        response = self._client.request(
            method=method.upper(),
            url=f"{self.api_base_url}{path}",
            headers=headers,
            content=canonical_body or None,
        )
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code", 0)
        if code != 0:
            raise RuntimeError(f"HashKey Merchant error: code={code} msg={payload.get('msg', '')}")
        return payload

    def _extract_flow_id(self, payment_url: str) -> str | None:
        path = urlparse(payment_url).path.rstrip("/")
        if not path:
            return None
        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return None
        return segments[-1]

    def _token_address_for_currency(self, currency: str) -> str:
        normalized = currency.upper()
        if normalized == "USDC":
            return self.usdc_address
        if normalized == "USDT":
            return self.usdt_address
        raise RuntimeError(f"Unsupported HSP currency: {currency}")

    def parse_webhook(self, body: bytes) -> HSPWebhookEvent:
        payload = json.loads(body.decode("utf-8"))
        payment_request_id = str(payload["payment_request_id"])
        cart_mandate_id = str(payload["cart_mandate_id"])
        payment_url = str(payload.get("payment_url", "")).strip()
        return HSPWebhookEvent(
            event_id=str(payload.get("request_id") or payload.get("event_id") or payment_request_id),
            payment_request_id=payment_request_id,
            cart_mandate_id=cart_mandate_id,
            flow_id=self._extract_flow_id(payment_url) if payment_url else None,
            status=str(payload["status"]).lower(),
            amount_cents=_cents_from_smallest_units(str(payload["amount"])),
            currency=str(payload["token"]).upper(),
            tx_hash=str(payload.get("tx_signature")) if payload.get("tx_signature") else None,
        )

    def build_webhook_signature(self, *, body: bytes, timestamp: str) -> str:
        signed_payload = f"{timestamp}.".encode("utf-8") + body
        return hmac.new(
            self.webhook_secret.encode("utf-8"),
            msg=signed_payload,
            digestmod=hashlib.sha256,
        ).hexdigest()

    def verify_webhook_signature(
        self,
        *,
        body: bytes,
        signature_header: str | None,
        legacy_signature: str | None = None,
        legacy_timestamp: str | None = None,
        now_ts: int | None = None,
    ) -> bool:
        if signature_header:
            parsed = self._parse_signature_header(signature_header)
            if parsed is None:
                return False
            timestamp, signature = parsed
        elif legacy_signature and legacy_timestamp:
            timestamp, signature = legacy_timestamp, legacy_signature
        else:
            return False
        try:
            timestamp_int = int(timestamp)
        except ValueError:
            return False
        current_ts = now_ts if now_ts is not None else _utc_timestamp()
        if abs(current_ts - timestamp_int) > self.webhook_tolerance_seconds:
            return False
        expected_signature = self.build_webhook_signature(body=body, timestamp=timestamp)
        return hmac.compare_digest(expected_signature, signature)

    @staticmethod
    def _parse_signature_header(signature_header: str) -> tuple[str, str] | None:
        timestamp = None
        signature = None
        for part in signature_header.split(","):
            trimmed = part.strip()
            if trimmed.startswith("t="):
                timestamp = trimmed[2:]
            elif trimmed.startswith("v1="):
                signature = trimmed[3:]
        if not timestamp or not signature:
            return None
        return timestamp, signature
