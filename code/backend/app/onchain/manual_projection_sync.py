from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.indexer.events import try_normalize_decoded_event
from app.indexer.evm_runtime import Web3AbiEventDecoder, build_subscriptions
from app.indexer.sql_projection import SqlProjectionStore
from app.onchain.adapter import DecodedChainEvent, RawLog
from app.onchain.receipts import ChainReceipt, ReceiptReader, get_receipt_reader


@dataclass(frozen=True)
class ManualProjectionSyncResult:
    tx_hash: str
    receipt_found: bool
    applied_events: int
    event_names: tuple[str, ...]
    listing_ids: tuple[str, ...]
    machine_ids: tuple[str, ...]


def sync_projection_from_tx_hash(
    *,
    tx_hash: str,
    session_factory,
    owner_resolver,
    settings: Settings | None = None,
    receipt_reader: ReceiptReader | None = None,
) -> ManualProjectionSyncResult:
    resolved_settings = settings or get_settings()
    reader = receipt_reader or get_receipt_reader()
    normalized_tx_hash = tx_hash.strip().lower()
    receipt = reader.get_receipt(normalized_tx_hash)
    if receipt is None:
        return ManualProjectionSyncResult(
            tx_hash=normalized_tx_hash,
            receipt_found=False,
            applied_events=0,
            event_names=(),
            listing_ids=(),
            machine_ids=(),
        )

    projection_store = SqlProjectionStore(
        session_factory=session_factory,
        owner_resolver=owner_resolver,
    )
    decoder = Web3AbiEventDecoder()
    subscriptions = {
        (subscription.contract_address.lower(), (subscription.topic0 or "").lower()): subscription
        for subscription in build_subscriptions(resolved_settings)
    }

    event_names: list[str] = []
    listing_ids: set[str] = set()
    machine_ids: set[str] = set()
    applied_events = 0

    logs = sorted(receipt.metadata.get("logs", []), key=lambda item: _coerce_log_index(item.get("logIndex")))
    for raw_entry in logs:
        normalized_event = _normalize_receipt_log(
            raw_entry=raw_entry,
            receipt=receipt,
            settings=resolved_settings,
            subscriptions=subscriptions,
            decoder=decoder,
        )
        if normalized_event is None:
            continue
        projection_store.apply(normalized_event)
        applied_events += 1
        event_names.append(normalized_event.event_name)
        payload = normalized_event.payload
        listing_id = getattr(payload, "listing_id", None)
        machine_id = getattr(payload, "machine_id", None)
        if listing_id is not None:
            listing_ids.add(str(listing_id))
        if machine_id is not None:
            machine_ids.add(str(machine_id))

    return ManualProjectionSyncResult(
        tx_hash=normalized_tx_hash,
        receipt_found=True,
        applied_events=applied_events,
        event_names=tuple(event_names),
        listing_ids=tuple(sorted(listing_ids)),
        machine_ids=tuple(sorted(machine_ids)),
    )


def _normalize_receipt_log(
    *,
    raw_entry: dict,
    receipt: ChainReceipt,
    settings: Settings,
    subscriptions: dict[tuple[str, str], object],
    decoder: Web3AbiEventDecoder,
):
    address = str(raw_entry.get("address", "")).lower()
    topics = tuple(str(topic).lower() for topic in raw_entry.get("topics", []))
    if not address or not topics:
        return None
    subscription = subscriptions.get((address, topics[0]))
    if subscription is None:
        return None

    block_number = _coerce_block_number(raw_entry.get("blockNumber"), default=receipt.block_number)
    block_hash = _coerce_hex(raw_entry.get("blockHash")) or f"0xreceiptblock{receipt.block_number:x}"
    transaction_hash = _coerce_hex(raw_entry.get("transactionHash")) or receipt.tx_hash
    raw_log = RawLog(
        chain_id=settings.onchain_chain_id,
        contract_name=subscription.contract_name,
        contract_address=subscription.contract_address.lower(),
        event_name=subscription.event_name,
        block_number=block_number,
        block_hash=block_hash,
        transaction_hash=transaction_hash,
        log_index=_coerce_log_index(raw_entry.get("logIndex")),
        data=_coerce_hex(raw_entry.get("data")) or "0x",
        topics=topics,
        removed=bool(raw_entry.get("removed", False)),
    )
    decoded = DecodedChainEvent(
        chain_id=raw_log.chain_id,
        contract_name=raw_log.contract_name,
        contract_address=raw_log.contract_address,
        event_name=raw_log.event_name,
        block_number=raw_log.block_number,
        block_hash=raw_log.block_hash,
        transaction_hash=raw_log.transaction_hash,
        log_index=raw_log.log_index,
        args=decoder.decode(subscription=subscription, raw_log=raw_log),
        removed=raw_log.removed,
    )
    return try_normalize_decoded_event(decoded)


def _coerce_hex(value) -> str:
    if value is None:
        return ""
    normalized = str(value).lower()
    return normalized if normalized.startswith("0x") else f"0x{normalized}"


def _coerce_block_number(value, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).lower()
    return int(text, 16) if text.startswith("0x") else int(text)


def _coerce_log_index(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = str(value).lower()
    return int(text, 16) if text.startswith("0x") else int(text)
