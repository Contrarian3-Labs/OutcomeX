"""On-chain adapter boundaries for EVM-compatible chains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence


def _hex_string(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "hex"):
        return str(value.hex())
    return str(value)


@dataclass(frozen=True)
class EventSubscription:
    """Subscription metadata for contract event polling."""

    contract_name: str
    contract_address: str
    event_name: str
    topic0: str | None = None


@dataclass(frozen=True)
class RawLog:
    """Raw log payload normalized from web3 responses."""

    chain_id: int
    contract_name: str
    contract_address: str
    event_name: str
    block_number: int
    block_hash: str
    transaction_hash: str
    log_index: int
    data: str
    topics: tuple[str, ...]
    removed: bool = False


@dataclass(frozen=True)
class DecodedChainEvent:
    """Decoded event used by the indexer layer."""

    chain_id: int
    contract_name: str
    contract_address: str
    event_name: str
    block_number: int
    block_hash: str
    transaction_hash: str
    log_index: int
    args: Mapping[str, Any]
    removed: bool = False


class EventDecoder(Protocol):
    """Boundary for decoding raw event logs into argument dictionaries."""

    def decode(self, *, subscription: EventSubscription, raw_log: RawLog) -> Mapping[str, Any]:
        ...


class ChainAdapter(Protocol):
    """Boundary that emits decoded chain events for the indexer."""

    def iter_events(self, *, from_block: int, to_block: int | None = None) -> Iterable[DecodedChainEvent]:
        ...


class PassthroughDecoder:
    """Fallback decoder used when upstream already provides decoded args."""

    def decode(self, *, subscription: EventSubscription, raw_log: RawLog) -> Mapping[str, Any]:
        return {
            "data": raw_log.data,
            "topics": list(raw_log.topics),
            "subscriptionEvent": subscription.event_name,
        }


class Web3ChainAdapter:
    """web3.py polling adapter with chunked block replay support."""

    def __init__(
        self,
        *,
        chain_id: int,
        subscriptions: Sequence[EventSubscription],
        web3_client: Any,
        decoder: EventDecoder | None = None,
        max_block_span: int = 2_000,
    ) -> None:
        self.chain_id = chain_id
        self._subscriptions = tuple(subscriptions)
        self._web3 = web3_client
        self._decoder = decoder or PassthroughDecoder()
        self._max_block_span = max(1, max_block_span)

    @classmethod
    def from_rpc_url(
        cls,
        *,
        rpc_url: str,
        chain_id: int,
        subscriptions: Sequence[EventSubscription],
        decoder: EventDecoder | None = None,
        max_block_span: int = 2_000,
        poa_compatible: bool = True,
    ) -> "Web3ChainAdapter":
        """Construct adapter with lazy imports so tests don't require web3 installed."""
        try:
            from web3 import Web3
            from web3.providers.rpc import HTTPProvider
        except ModuleNotFoundError as exc:
            raise RuntimeError("web3.py is required for Web3ChainAdapter.from_rpc_url") from exc

        web3_client = Web3(HTTPProvider(rpc_url))
        if poa_compatible:
            try:
                from web3.middleware import ExtraDataToPOAMiddleware

                web3_client.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except Exception:
                try:
                    from web3.middleware import geth_poa_middleware

                    web3_client.middleware_onion.inject(geth_poa_middleware, layer=0)
                except Exception:
                    pass

        return cls(
            chain_id=chain_id,
            subscriptions=subscriptions,
            web3_client=web3_client,
            decoder=decoder,
            max_block_span=max_block_span,
        )

    def iter_events(self, *, from_block: int, to_block: int | None = None) -> Iterable[DecodedChainEvent]:
        latest_block = int(to_block if to_block is not None else self._web3.eth.block_number)
        block_cursor = max(0, from_block)

        while block_cursor <= latest_block:
            batch_to_block = min(block_cursor + self._max_block_span - 1, latest_block)
            for subscription in self._subscriptions:
                yield from self._load_subscription_batch(
                    subscription=subscription,
                    from_block=block_cursor,
                    to_block=batch_to_block,
                )
            block_cursor = batch_to_block + 1

    def _load_subscription_batch(
        self,
        *,
        subscription: EventSubscription,
        from_block: int,
        to_block: int,
    ) -> Iterator[DecodedChainEvent]:
        filter_params: dict[str, Any] = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": subscription.contract_address,
        }
        if subscription.topic0:
            filter_params["topics"] = [subscription.topic0]

        logs = self._web3.eth.get_logs(filter_params)
        for web3_log in logs:
            raw_log = self._normalize_raw_log(subscription=subscription, web3_log=web3_log)
            decoded_args = self._decoder.decode(subscription=subscription, raw_log=raw_log)
            yield DecodedChainEvent(
                chain_id=raw_log.chain_id,
                contract_name=raw_log.contract_name,
                contract_address=raw_log.contract_address,
                event_name=raw_log.event_name,
                block_number=raw_log.block_number,
                block_hash=raw_log.block_hash,
                transaction_hash=raw_log.transaction_hash,
                log_index=raw_log.log_index,
                args=decoded_args,
                removed=raw_log.removed,
            )

    def _normalize_raw_log(self, *, subscription: EventSubscription, web3_log: Mapping[str, Any]) -> RawLog:
        return RawLog(
            chain_id=self.chain_id,
            contract_name=subscription.contract_name,
            contract_address=subscription.contract_address.lower(),
            event_name=subscription.event_name,
            block_number=int(web3_log["blockNumber"]),
            block_hash=_hex_string(web3_log.get("blockHash")).lower(),
            transaction_hash=_hex_string(web3_log.get("transactionHash")).lower(),
            log_index=int(web3_log["logIndex"]),
            data=_hex_string(web3_log.get("data")).lower(),
            topics=tuple(_hex_string(topic).lower() for topic in web3_log.get("topics", ())),
            removed=bool(web3_log.get("removed", False)),
        )
