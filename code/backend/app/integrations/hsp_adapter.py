from dataclasses import dataclass
from uuid import uuid4


@dataclass(slots=True)
class HSPPaymentIntent:
    provider: str
    provider_reference: str
    checkout_url: str


class HSPAdapter:
    """
    HSP boundary adapter.
    This is intentionally a mock adapter boundary, not a real integration.
    """

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def create_payment_intent(self, order_id: str, amount_cents: int, currency: str) -> HSPPaymentIntent:
        reference = f"hsp_{uuid4().hex[:18]}"
        return HSPPaymentIntent(
            provider="hsp",
            provider_reference=reference,
            checkout_url=f"{self.base_url}/checkout/{reference}?order_id={order_id}&amount={amount_cents}&currency={currency}",
        )

