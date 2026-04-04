"""Tests for domain event normalization."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.indexer.events import normalize_decoded_event
from app.onchain.adapter import DecodedChainEvent


def test_normalize_machine_asset_event_shapes_addresses_and_ints() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="MachineAsset",
        contract_address="0xABCD00000000000000000000000000000000EF12",
        event_name="MachineAssetRegistered",
        block_number=42,
        block_hash="0xblockhash",
        transaction_hash="0xTXHASH",
        log_index=3,
        args={
            "machineId": "MA-0001",
            "owner": "0x1234567890ABCDEF1234567890ABCDEF12345678",
            "metadataURI": "ipfs://machine",
            "pwrQuota": "15",
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.event_id == "177:42:0xtxhash:3"
    assert normalized.contract_address == "0xabcd00000000000000000000000000000000ef12"
    assert normalized.payload.machine_id == "MA-0001"
    assert normalized.payload.owner == "0x1234567890abcdef1234567890abcdef12345678"
    assert normalized.payload.pwr_quota == 15


def test_unknown_event_name_raises_value_error() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="MachineAsset",
        contract_address="0xabcd00000000000000000000000000000000ef12",
        event_name="DoesNotExist",
        block_number=1,
        block_hash="0x1",
        transaction_hash="0x2",
        log_index=0,
        args={},
    )

    with pytest.raises(ValueError, match="Unsupported event_name"):
        normalize_decoded_event(decoded)
