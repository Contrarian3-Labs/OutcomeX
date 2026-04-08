"""Domain event models and normalization for indexed chain logs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.onchain.adapter import DecodedChainEvent

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _normalize_address(value: Any) -> str:
    return str(value).lower()


def _normalize_bytes32(value: Any) -> str:
    if isinstance(value, bytes):
        return f"0x{value.hex()}"
    return str(value).lower()


def _as_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.startswith("0x"):
            return int(value, 16)
        return int(value)
    raise ValueError(f"Expected integer-like value for '{field_name}', got {value!r}")


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    raise ValueError(f"Expected bool-like value for '{field_name}', got {value!r}")


def _pick(args: Mapping[str, Any], *names: str, required: bool = True, default: Any = None) -> Any:
    for name in names:
        if name in args:
            return args[name]
    if required:
        raise ValueError(f"Missing required args key from {names}")
    return default


@dataclass(frozen=True)
class MachineAssetEvent:
    machine_id: str
    owner: str
    metadata_uri: str | None
    pwr_quota: int | None


@dataclass(frozen=True)
class OrderLifecycleEvent:
    order_id: str
    machine_id: str | None
    buyer: str | None
    status: str
    amount_wei: int | None
    cancelled_at: int | None = None
    cancelled_as_expired: bool | None = None
    payer: str | None = None
    payment_token: str | None = None
    payment_source: str | None = None
    settlement_beneficiary: str | None = None
    dividend_eligible: bool | None = None
    refund_authorized: bool | None = None


@dataclass(frozen=True)
class SettlementSplitEvent:
    order_id: str
    machine_id: str | None
    recipient: str
    role: str | None
    amount_wei: int
    bps: int | None


@dataclass(frozen=True)
class RevenueClaimedEvent:
    machine_id: str | None
    account: str
    amount_wei: int
    claim_nonce: int | None
    claim_kind: str
    token_address: str | None = None
    remaining_account_balance_wei: int | None = None
    remaining_claimable_wei: int | None = None
    remaining_unsettled_wei: int | None = None


@dataclass(frozen=True)
class TransferGuardUpdatedEvent:
    asset_id: str
    is_transferable: bool
    reason: str | None
    active_tasks: int | None
    unsettled_revenue: int | None


@dataclass(frozen=True)
class PWRMintedEvent:
    account: str
    amount_wei: int
    reason: str | None


class UnsupportedEventNameError(ValueError):
    pass


DomainPayload = (
    MachineAssetEvent
    | OrderLifecycleEvent
    | SettlementSplitEvent
    | RevenueClaimedEvent
    | TransferGuardUpdatedEvent
    | PWRMintedEvent
)


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    chain_id: int
    contract_name: str
    contract_address: str
    event_name: str
    block_number: int
    block_hash: str
    transaction_hash: str
    log_index: int
    payload: DomainPayload


ORDER_EVENT_NAMES = {
    "OrderCreated",
    "OrderClassified",
    "OrderPaid",
    "PaymentFinalized",
    "OrderCancelled",
    "PreviewReady",
    "OrderSettled",
    "Settled",
}


def _normalize_machine_asset_payload(event: DecodedChainEvent) -> MachineAssetEvent:
    args = event.args
    if event.event_name == "MachineMinted":
        return MachineAssetEvent(
            machine_id=str(_as_int(_pick(args, "machineId", "machine_id"), field_name="machine_id")),
            owner=_normalize_address(_pick(args, "owner")),
            metadata_uri=_pick(args, "tokenURI", "tokenUri", "metadataURI", required=False),
            pwr_quota=None,
        )

    if event.event_name == "Transfer":
        return MachineAssetEvent(
            machine_id=str(_as_int(_pick(args, "tokenId", "machineId"), field_name="machine_id")),
            owner=_normalize_address(_pick(args, "to")),
            metadata_uri=None,
            pwr_quota=None,
        )

    raise UnsupportedEventNameError(f"Unsupported machine event_name '{event.event_name}'")


def _settlement_kind_to_order_status(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized.isdigit():
            value = int(normalized)
        else:
            name_mapping = {
                "CONFIRMED": "CONFIRMED",
                "REJECTEDVALIDPREVIEW": "REJECTED",
                "REJECTED_VALID_PREVIEW": "REJECTED",
                "FAILEDORNOVALIDPREVIEW": "REFUNDED",
                "FAILED_OR_NO_VALID_PREVIEW": "REFUNDED",
            }
            if normalized in name_mapping:
                return name_mapping[normalized]
            raise ValueError(f"Unsupported settlement kind '{value}'")

    if isinstance(value, int):
        int_mapping = {
            0: "CONFIRMED",
            1: "REJECTED",
            2: "REFUNDED",
        }
        if value in int_mapping:
            return int_mapping[value]
        raise ValueError(f"Unsupported settlement kind '{value}'")

    raise ValueError(f"Unsupported settlement kind '{value}'")


def _normalize_order_payload(event_name: str, args: Mapping[str, Any]) -> OrderLifecycleEvent:
    status_by_event = {
        "OrderCreated": "CREATED",
        "OrderClassified": "CLASSIFIED",
        "OrderPaid": "PAID",
        "PaymentFinalized": "PAID",
        "OrderCancelled": "CANCELLED",
        "PreviewReady": "PREVIEW_READY",
    }
    status = status_by_event.get(event_name)
    if event_name in {"OrderSettled", "Settled"}:
        status = _settlement_kind_to_order_status(_pick(args, "kind"))

    if status is None:
        raise UnsupportedEventNameError(f"Unsupported order event_name '{event_name}'")

    amount_wei: int | None = None
    if event_name == "OrderCreated":
        gross_amount = _pick(args, "grossAmount", "amountWei", required=False)
        if gross_amount is not None:
            amount_wei = _as_int(gross_amount, field_name="gross_amount")
    elif event_name in {"OrderPaid", "PaymentFinalized"}:
        gross_amount = _pick(args, "grossAmount", "amountWei", required=False)
        if gross_amount is not None:
            amount_wei = _as_int(gross_amount, field_name="gross_amount")
    elif event_name == "OrderCancelled":
        amount_wei = None
    elif event_name in {"OrderSettled", "Settled"}:
        gross_amount = _pick(args, "grossAmount", required=False)
        if gross_amount is not None:
            amount_wei = _as_int(gross_amount, field_name="gross_amount")
        else:
            refund = _pick(args, "refundToBuyer", required=False, default=0)
            platform_share = _pick(args, "platformShare", required=False, default=0)
            machine_share = _pick(args, "machineShare", required=False, default=0)
            amount_wei = (
                _as_int(refund, field_name="refund_to_buyer")
                + _as_int(platform_share, field_name="platform_share")
                + _as_int(machine_share, field_name="machine_share")
            )

    return OrderLifecycleEvent(
        order_id=str(_as_int(_pick(args, "orderId", "order_id"), field_name="order_id")),
        machine_id=(
            str(_as_int(_pick(args, "machineId", "machine_id", required=False), field_name="machine_id"))
            if _pick(args, "machineId", "machine_id", required=False) is not None
            else None
        ),
        buyer=(
            _normalize_address(_pick(args, "buyer", "owner", required=False))
            if _pick(args, "buyer", "owner", required=False) is not None
            else None
        ),
        status=status,
        amount_wei=amount_wei,
        cancelled_at=(
            _as_int(_pick(args, "cancelledAt", "canceledAt", required=False), field_name="cancelled_at")
            if _pick(args, "cancelledAt", "canceledAt", required=False) is not None
            else None
        ),
        cancelled_as_expired=(
            _as_bool(_pick(args, "expired", required=False), field_name="expired")
            if _pick(args, "expired", required=False) is not None
            else None
        ),
        payer=(
            _normalize_address(_pick(args, "payer", required=False))
            if _pick(args, "payer", required=False) is not None
            else None
        ),
        payment_token=(
            _normalize_address(_pick(args, "paymentToken", "token", required=False))
            if _pick(args, "paymentToken", "token", required=False) is not None
            else None
        ),
        payment_source=(
            _normalize_bytes32(_pick(args, "paymentSource", required=False))
            if _pick(args, "paymentSource", required=False) is not None
            else None
        ),
        settlement_beneficiary=(
            _normalize_address(_pick(args, "settlementBeneficiary", required=False))
            if _pick(args, "settlementBeneficiary", required=False) is not None
            else None
        ),
        dividend_eligible=(
            _as_bool(_pick(args, "dividendEligible", required=False), field_name="dividend_eligible")
            if _pick(args, "dividendEligible", required=False) is not None
            else None
        ),
        refund_authorized=(
            _as_bool(_pick(args, "refundAuthorized", required=False), field_name="refund_authorized")
            if _pick(args, "refundAuthorized", required=False) is not None
            else None
        ),
    )


def _normalize_revenue_payload(event_name: str, args: Mapping[str, Any]) -> SettlementSplitEvent | RevenueClaimedEvent:
    if event_name == "RevenueAccrued":
        dividend_eligible = _as_bool(
            _pick(args, "dividendEligible", "dividend_eligible"),
            field_name="dividend_eligible",
        )
        return SettlementSplitEvent(
            order_id=str(_as_int(_pick(args, "orderId", "order_id"), field_name="order_id")),
            machine_id=(
                str(_as_int(_pick(args, "machineId", "machine_id", required=False), field_name="machine_id"))
                if _pick(args, "machineId", "machine_id", required=False) is not None
                else None
            ),
            recipient=_normalize_address(_pick(args, "machineOwner", "recipient", "to")),
            role="MACHINE_OWNER_DIVIDEND" if dividend_eligible else "MACHINE_OWNER_NON_DIVIDEND",
            amount_wei=_as_int(_pick(args, "amount", "amountWei", "amount_wei"), field_name="amount"),
            bps=None,
        )

    if event_name in {
        "RevenueClaimed",
        "RefundClaimed",
        "PlatformRevenueClaimed",
        "MachineRevenueClaimedDetailed",
        "RefundClaimedDetailed",
        "PlatformRevenueClaimedDetailed",
    }:
        account_field_by_event = {
            "RevenueClaimed": ("machineOwner", "account"),
            "RefundClaimed": ("buyer", "account"),
            "PlatformRevenueClaimed": ("treasury", "account"),
            "MachineRevenueClaimedDetailed": ("machineOwner", "account"),
            "RefundClaimedDetailed": ("buyer", "account"),
            "PlatformRevenueClaimedDetailed": ("treasury", "account"),
        }
        claim_kind_by_event = {
            "RevenueClaimed": "machine_revenue",
            "RefundClaimed": "refund",
            "PlatformRevenueClaimed": "platform_revenue",
            "MachineRevenueClaimedDetailed": "machine_revenue",
            "RefundClaimedDetailed": "refund",
            "PlatformRevenueClaimedDetailed": "platform_revenue",
        }
        account_fields = account_field_by_event[event_name]
        return RevenueClaimedEvent(
            machine_id=(
                str(_as_int(_pick(args, "machineId", "machine_id", required=False), field_name="machine_id"))
                if _pick(args, "machineId", "machine_id", required=False) is not None
                else None
            ),
            account=_normalize_address(_pick(args, *account_fields)),
            amount_wei=_as_int(_pick(args, "amount", "amountWei", "amount_wei"), field_name="amount"),
            claim_nonce=None,
            claim_kind=claim_kind_by_event[event_name],
            token_address=(
                _normalize_address(_pick(args, "token", "tokenAddress", required=False))
                if _pick(args, "token", "tokenAddress", required=False) is not None
                else None
            ),
            remaining_account_balance_wei=(
                _as_int(
                    _pick(
                        args,
                        "remainingRefundableAfter",
                        "remainingPlatformAccruedAfter",
                        required=False,
                    ),
                    field_name="remaining_account_balance",
                )
                if _pick(
                    args,
                    "remainingRefundableAfter",
                    "remainingPlatformAccruedAfter",
                    required=False,
                )
                is not None
                else None
            ),
            remaining_claimable_wei=(
                _as_int(
                    _pick(args, "remainingClaimableForMachineOwnerAfter", required=False),
                    field_name="remaining_claimable",
                )
                if _pick(args, "remainingClaimableForMachineOwnerAfter", required=False) is not None
                else None
            ),
            remaining_unsettled_wei=(
                _as_int(
                    _pick(args, "remainingUnsettledRevenueByMachineAfter", required=False),
                    field_name="remaining_unsettled",
                )
                if _pick(args, "remainingUnsettledRevenueByMachineAfter", required=False) is not None
                else None
            ),
        )

    raise UnsupportedEventNameError(f"Unsupported revenue event_name '{event_name}'")


def _normalize_token_payload(event: DecodedChainEvent) -> PWRMintedEvent:
    args = event.args
    from_address = _normalize_address(_pick(args, "from"))
    to_address = _normalize_address(_pick(args, "to"))
    if from_address == ZERO_ADDRESS and to_address != ZERO_ADDRESS:
        return PWRMintedEvent(
            account=to_address,
            amount_wei=_as_int(_pick(args, "value", "amount"), field_name="value"),
            reason="MINT",
        )

    raise UnsupportedEventNameError(f"Unsupported token transfer event for '{event.contract_name}'")


def normalize_decoded_event(event: DecodedChainEvent) -> NormalizedEvent:
    event_name = event.event_name
    contract_name = event.contract_name

    payload: DomainPayload
    if event_name in {"MachineMinted"}:
        payload = _normalize_machine_asset_payload(event)
    elif event_name == "Transfer" and (
        contract_name == "MachineAssetNFT" or "tokenId" in event.args
    ):
        payload = _normalize_machine_asset_payload(event)
    elif event_name in ORDER_EVENT_NAMES:
        payload = _normalize_order_payload(event_name, event.args)
    elif event_name in {
        "RevenueAccrued",
        "RevenueClaimed",
        "RefundClaimed",
        "PlatformRevenueClaimed",
        "MachineRevenueClaimedDetailed",
        "RefundClaimedDetailed",
        "PlatformRevenueClaimedDetailed",
    }:
        payload = _normalize_revenue_payload(event_name, event.args)
    elif event_name == "Transfer" and contract_name in {"PWRToken", "SimpleERC20"}:
        payload = _normalize_token_payload(event)
    elif event_name == "TransferGuardUpdated":
        payload = TransferGuardUpdatedEvent(
            asset_id=str(_pick(event.args, "assetId", "machineId", "tokenId")),
            is_transferable=bool(_pick(event.args, "isTransferable", "transferable")),
            reason=_pick(event.args, "reason", "reasonCode", required=False),
            active_tasks=None,
            unsettled_revenue=None,
        )
    else:
        raise UnsupportedEventNameError(f"Unsupported event_name '{event_name}'")

    tx_hash = str(event.transaction_hash).lower()
    return NormalizedEvent(
        event_id=f"{event.chain_id}:{event.block_number}:{tx_hash}:{event.log_index}",
        chain_id=event.chain_id,
        contract_name=event.contract_name,
        contract_address=event.contract_address.lower(),
        event_name=event.event_name,
        block_number=event.block_number,
        block_hash=str(event.block_hash).lower(),
        transaction_hash=tx_hash,
        log_index=event.log_index,
        payload=payload,
    )


def try_normalize_decoded_event(event: DecodedChainEvent) -> NormalizedEvent | None:
    try:
        return normalize_decoded_event(event)
    except UnsupportedEventNameError:
        return None
