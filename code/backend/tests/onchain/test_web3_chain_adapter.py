from __future__ import annotations

from app.onchain.adapter import EventSubscription, RawLog, Web3ChainAdapter


class _DecoderStub:
    def decode(self, *, subscription: EventSubscription, raw_log: RawLog):
        return {"subscription_event": subscription.event_name, "tx_hash": raw_log.transaction_hash}


class _EthStub:
    def __init__(self) -> None:
        self.block_number = 123
        self.calls: list[dict] = []

    def get_logs(self, filter_params):
        self.calls.append(filter_params)
        topics = filter_params.get("topics")
        if topics and isinstance(topics[0], list):
            return [
                {
                    "address": "0x1000000000000000000000000000000000000001",
                    "blockNumber": 10,
                    "blockHash": "0xaaa",
                    "transactionHash": "0x111",
                    "logIndex": 0,
                    "data": "0x",
                    "topics": ["0xtopic-a"],
                },
                {
                    "address": "0x2000000000000000000000000000000000000002",
                    "blockNumber": 11,
                    "blockHash": "0xbbb",
                    "transactionHash": "0x222",
                    "logIndex": 1,
                    "data": "0x",
                    "topics": ["0xtopic-b"],
                },
            ]
        return []


class _Web3Stub:
    def __init__(self) -> None:
        self.eth = _EthStub()


def test_web3_chain_adapter_batches_topic0_subscriptions_into_one_get_logs_call() -> None:
    web3 = _Web3Stub()
    adapter = Web3ChainAdapter(
        chain_id=133,
        subscriptions=(
            EventSubscription(
                contract_name="OrderBook",
                contract_address="0x1000000000000000000000000000000000000001",
                event_name="OrderCreated",
                topic0="0xtopic-a",
            ),
            EventSubscription(
                contract_name="RevenueVault",
                contract_address="0x2000000000000000000000000000000000000002",
                event_name="RevenueAccrued",
                topic0="0xtopic-b",
            ),
        ),
        web3_client=web3,
        decoder=_DecoderStub(),
        max_block_span=500,
    )

    events = list(adapter.iter_events(from_block=0, to_block=20))

    assert len(web3.eth.calls) == 1
    assert sorted(web3.eth.calls[0]["address"]) == [
        "0x1000000000000000000000000000000000000001",
        "0x2000000000000000000000000000000000000002",
    ]
    assert web3.eth.calls[0]["topics"] == [["0xtopic-a", "0xtopic-b"]]
    assert [event.event_name for event in events] == ["OrderCreated", "RevenueAccrued"]
    assert [event.args["subscription_event"] for event in events] == ["OrderCreated", "RevenueAccrued"]
