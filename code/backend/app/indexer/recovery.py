from __future__ import annotations

from typing import Callable
from uuid import NAMESPACE_URL, uuid5


PAYMENT_SOURCE_USDC_EIP3009 = "0x23ed9d0af8d67977569424852c533e3609bc894a1fe39a62f142bd1068a7779b"
PAYMENT_SOURCE_USDT_DIRECT = "0xae6386a52d54d0ca9a0e35dc6310e9614d47016a3af30009bc0c65210d89961f"
PAYMENT_SOURCE_PWR_DIRECT = "0x1614a27787ef698d45214c1c51f354586a4ba00658b78da3fa164b4302c7bc26"
PAYMENT_SOURCE_HSP_CONFIRMED = "0x8771ed48461cf41393fcc4576d92404baaa014511cb3d5ca476b7a553d62f716"


def projection_uuid(kind: str, natural_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"outcomex:{kind}:{natural_key}"))


def resolve_projected_user_id(
    owner_resolver: Callable[[str], str | None] | None,
    chain_address: str | None,
    *,
    fallback_prefix: str,
    natural_key: str | None = None,
) -> str:
    if chain_address:
        normalized = chain_address.lower()
        if owner_resolver is not None:
            resolved = owner_resolver(normalized)
            if resolved:
                return resolved
        return normalized
    suffix = natural_key or "unknown"
    return f"{fallback_prefix}-{suffix}"[:64]


def fallback_machine_display_name(onchain_machine_id: str, metadata_uri: str | None = None) -> str:
    if metadata_uri and "primary-issuance" in metadata_uri:
        return f"Primary Issuance Machine #{onchain_machine_id}"
    return f"OutcomeX Machine #{onchain_machine_id}"


def placeholder_chat_session_id(onchain_order_id: str) -> str:
    return f"recovered-order-{onchain_order_id}"[:64]


def placeholder_user_prompt(onchain_order_id: str) -> str:
    return f"[Recovered from chain] Order #{onchain_order_id}"


def placeholder_plan_summary(onchain_order_id: str) -> str:
    return f"Recovered onchain order #{onchain_order_id}"


def payment_provider_from_source(payment_source: str | None) -> str:
    normalized = (payment_source or "").lower()
    if normalized == PAYMENT_SOURCE_HSP_CONFIRMED:
        return "hsp"
    return "onchain_router"
