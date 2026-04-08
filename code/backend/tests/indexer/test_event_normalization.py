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


def test_normalize_payment_finalized_carries_paid_order_context() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="OrderPaymentRouter",
        contract_address="0x6000000000000000000000000000000000000006",
        event_name="PaymentFinalized",
        block_number=91,
        block_hash="0xblock-91",
        transaction_hash="0xbbc",
        log_index=6,
        args={
            "orderId": "12",
            "machineId": "5",
            "buyer": "0xB00000000000000000000000000000000000B000",
            "payer": "0xA00000000000000000000000000000000000A000",
            "paymentToken": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
            "grossAmount": "1000",
            "paymentSource": "0x1234",
            "settlementBeneficiary": "0xC00000000000000000000000000000000000C000",
            "dividendEligible": True,
            "refundAuthorized": True,
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.order_id == "12"
    assert normalized.payload.status == "PAID"
    assert normalized.payload.buyer == "0xb00000000000000000000000000000000000b000"
    assert normalized.payload.payer == "0xa00000000000000000000000000000000000a000"
    assert normalized.payload.payment_token == "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"
    assert normalized.payload.payment_source == "0x1234"
    assert normalized.payload.settlement_beneficiary == "0xc00000000000000000000000000000000000c000"
    assert normalized.payload.dividend_eligible is True
    assert normalized.payload.refund_authorized is True


def test_normalize_refund_claimed_detailed_carries_token_and_remaining_balance() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="SettlementController",
        contract_address="0x5000000000000000000000000000000000000005",
        event_name="RefundClaimedDetailed",
        block_number=91,
        block_hash="0xblock-91",
        transaction_hash="0xbbc",
        log_index=6,
        args={
            "buyer": "0xB00000000000000000000000000000000000B000",
            "token": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
            "amount": "700",
            "remainingRefundableAfter": "0",
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.account == "0xb00000000000000000000000000000000000b000"
    assert normalized.payload.claim_kind == "refund"
    assert normalized.payload.token_address == "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"
    assert normalized.payload.amount_wei == 700
    assert normalized.payload.remaining_account_balance_wei == 0


def test_normalize_platform_claimed_detailed_carries_zero_address_token() -> None:
    decoded = DecodedChainEvent(
        chain_id=177,
        contract_name="SettlementController",
        contract_address="0x5000000000000000000000000000000000000005",
        event_name="PlatformRevenueClaimedDetailed",
        block_number=92,
        block_hash="0xblock-92",
        transaction_hash="0xbbd",
        log_index=7,
        args={
            "treasury": "0xC00000000000000000000000000000000000C000",
            "token": "0x0000000000000000000000000000000000000000",
            "amount": "100",
            "remainingPlatformAccruedAfter": "0",
        },
    )

    normalized = normalize_decoded_event(decoded)

    assert normalized.payload.account == "0xc00000000000000000000000000000000000c000"
    assert normalized.payload.claim_kind == "platform_revenue"
    assert normalized.payload.token_address == "0x0000000000000000000000000000000000000000"
    assert normalized.payload.amount_wei == 100
    assert normalized.payload.remaining_account_balance_wei == 0


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
