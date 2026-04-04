"""Domain event models and normalization for indexed chain logs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.onchain.adapter import DecodedChainEvent


def _normalize_address(value: Any) -> str:
    return str(value).lower()


def _as_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.startswith("0x"):
            return int(value, 16)
        return int(value)
    raise ValueError(f"Expected integer-like value for '{field_name}', got {value!r}")


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


@dataclass(frozen=True)
class SettlementSplitEvent:
    order_id: str
    recipient: str
    role: str | None
    amount_wei: int
    bps: int | None


@dataclass(frozen=True)
class RevenueClaimedEvent:
    account: str
    amount_wei: int
    claim_nonce: int | None


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
    "OrderOpened",
    "OrderMatched",
    "OrderResultSubmitted",
    "OrderResultConfirmed",
    "OrderSettled",
    "OrderCancelled",
}


def normalize_decoded_event(event: DecodedChainEvent) -> NormalizedEvent:
    event_name = event.event_name
    args = event.args

    if event_name in {"MachineAssetRegistered", "MachineAssetUpdated"}:
        payload: DomainPayload = MachineAssetEvent(
            machine_id=str(_pick(args, "machineId", "machine_id")),
            owner=_normalize_address(_pick(args, "owner", "machineOwner", "machine_owner")),
            metadata_uri=_pick(args, "metadataURI", "metadataUri", "metadata_uri", required=False),
            pwr_quota=(
                _as_int(_pick(args, "pwrQuota", "pwr_quota", required=False), field_name="pwr_quota")
                if _pick(args, "pwrQuota", "pwr_quota", required=False) is not None
                else None
            ),
        )
    elif event_name in ORDER_EVENT_NAMES:
        derived_status = event_name.replace("Order", "").upper()
        payload = OrderLifecycleEvent(
            order_id=str(_pick(args, "orderId", "order_id")),
            machine_id=(
                str(_pick(args, "machineId", "machine_id", required=False))
                if _pick(args, "machineId", "machine_id", required=False) is not None
                else None
            ),
            buyer=(
                _normalize_address(_pick(args, "buyer", "owner", required=False))
                if _pick(args, "buyer", "owner", required=False) is not None
                else None
            ),
            status=str(_pick(args, "status", required=False, default=derived_status)).upper(),
            amount_wei=(
                _as_int(_pick(args, "amountWei", "amount_wei", required=False), field_name="amount_wei")
                if _pick(args, "amountWei", "amount_wei", required=False) is not None
                else None
            ),
        )
    elif event_name == "SettlementSplit":
        payload = SettlementSplitEvent(
            order_id=str(_pick(args, "orderId", "order_id")),
            recipient=_normalize_address(_pick(args, "recipient", "to")),
            role=_pick(args, "role", required=False),
            amount_wei=_as_int(_pick(args, "amountWei", "amount_wei"), field_name="amount_wei"),
            bps=(
                _as_int(_pick(args, "bps", "basisPoints", required=False), field_name="bps")
                if _pick(args, "bps", "basisPoints", required=False) is not None
                else None
            ),
        )
    elif event_name == "RevenueClaimed":
        payload = RevenueClaimedEvent(
            account=_normalize_address(_pick(args, "account", "claimer")),
            amount_wei=_as_int(_pick(args, "amountWei", "amount_wei"), field_name="amount_wei"),
            claim_nonce=(
                _as_int(_pick(args, "nonce", "claimNonce", required=False), field_name="claim_nonce")
                if _pick(args, "nonce", "claimNonce", required=False) is not None
                else None
            ),
        )
    elif event_name == "TransferGuardUpdated":
        payload = TransferGuardUpdatedEvent(
            asset_id=str(_pick(args, "assetId", "machineId", "tokenId")),
            is_transferable=bool(_pick(args, "isTransferable", "transferable")),
            reason=_pick(args, "reason", "reasonCode", required=False),
            active_tasks=(
                _as_int(_pick(args, "activeTasks", required=False), field_name="active_tasks")
                if _pick(args, "activeTasks", required=False) is not None
                else None
            ),
            unsettled_revenue=(
                _as_int(
                    _pick(args, "unsettledRevenue", "pendingRevenue", required=False),
                    field_name="unsettled_revenue",
                )
                if _pick(args, "unsettledRevenue", "pendingRevenue", required=False) is not None
                else None
            ),
        )
    elif event_name == "PWRMinted":
        payload = PWRMintedEvent(
            account=_normalize_address(_pick(args, "to", "account")),
            amount_wei=_as_int(_pick(args, "amountWei", "amount", "amount_wei"), field_name="amount_wei"),
            reason=_pick(args, "reason", required=False),
        )
    else:
        raise ValueError(f"Unsupported event_name '{event_name}'")

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
