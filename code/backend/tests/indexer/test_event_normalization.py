"""Tests for domain event normalization."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.indexer.events import normalize_decoded_event, try_normalize_decoded_event
from app.onchain.adapter import DecodedChainEvent


def test_normalize_machine_minted_shapes_addresses_and_ints() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="MachineAssetNFT",
        contract_address="0xABCD00000000000000000000000000000000EF12",
        event_name="MachineMinted",
        block_number=42,
        block_hash="0xblockhash",
        transaction_hash="0xTXHASH",
        log_index=3,
        args={
            "machineId": "1",
            "owner": "0x1234567890ABCDEF1234567890ABCDEF12345678",
            "tokenURI": "ipfs://machine",
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.event_id == "177:42:0xtxhash:3"
    assert normalized.contract_address == "0xabcd00000000000000000000000000000000ef12"
    assert normalized.payload.machine_id == "1"
    assert normalized.payload.owner == "0x1234567890abcdef1234567890abcdef12345678"
    assert normalized.payload.metadata_uri == "ipfs://machine"


def test_normalize_order_settled_maps_kind_to_order_status() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name="OrderSettled",
        block_number=77,
        block_hash="0xblock-77",
        transaction_hash="0xaaa",
        log_index=1,
        args={
            "orderId": "9",
            "machineId": "3",
            "kind": 2,
            "refundToBuyer": "700",
            "platformShare": "30",
            "machineShare": "270",
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.order_id == "9"
    assert normalized.payload.status == "REFUNDED"
    assert normalized.payload.amount_wei == 1000


def test_normalize_order_cancelled_carries_expiry_truth() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name="OrderCancelled",
        block_number=78,
        block_hash="0xblock-78",
        transaction_hash="0xaab",
        log_index=2,
        args={
            "orderId": "10",
            "machineId": "4",
            "cancelledBy": "0xC00000000000000000000000000000000000C000",
            "cancelledAt": "1712553600",
            "expired": True,
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.order_id == "10"
    assert normalized.payload.machine_id == "4"
    assert normalized.payload.status == "CANCELLED"
    assert normalized.payload.cancelled_at == 1712553600
    assert normalized.payload.cancelled_as_expired is True


def test_normalize_revenue_accrued_to_settlement_split_payload() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="RevenueVault",
        contract_address="0x4000000000000000000000000000000000000004",
        event_name="RevenueAccrued",
        block_number=90,
        block_hash="0xblock-90",
        transaction_hash="0xbbb",
        log_index=5,
        args={
            "orderId": "11",
            "machineOwner": "0xF00000000000000000000000000000000000F000",
            "amount": "1234",
            "dividendEligible": True,
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.order_id == "11"
    assert normalized.payload.recipient == "0xf00000000000000000000000000000000000f000"
    assert normalized.payload.amount_wei == 1234
    assert normalized.payload.role == "MACHINE_OWNER_DIVIDEND"


def test_try_normalize_unknown_event_returns_none() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name="PaymentAdapterSet",
        block_number=1,
        block_hash="0x1",
        transaction_hash="0x2",
        log_index=0,
        args={},
    )

    assert try_normalize_decoded_event(decoded) is None


def test_unknown_event_name_raises_value_error() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name="PaymentAdapterSet",
        block_number=1,
        block_hash="0x1",
        transaction_hash="0x2",
        log_index=0,
        args={},
    )

    with pytest.raises(ValueError, match="Unsupported event_name"):
        normalize_decoded_event(decoded)
