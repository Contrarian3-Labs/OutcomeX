from datetime import datetime, timezone

from app.integrations.onchain_broadcaster import OnchainBroadcaster
from app.onchain.event_decoder import ORDER_CREATED_TOPIC0
from app.onchain.order_writer import OrderWriteResult
from app.onchain.receipts import ChainReceipt

BUYER_ADDRESS = "0x1111111111111111111111111111111111111111"


class StubReceiptReader:
    def __init__(self, receipt: ChainReceipt | None) -> None:
        self._receipt = receipt

    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        return self._receipt


def _write_result() -> OrderWriteResult:
    return OrderWriteResult(
        tx_hash="0xabc123",
        submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
        chain_id=133,
        contract_name="OrderPaymentRouter",
        contract_address="0x0000000000000000000000000000000000000134",
        method_name="payOrderByAdapter",
        idempotency_key="idempotency",
        payload={"buyer": BUYER_ADDRESS},
    )


def _order_created_log(order_id: int) -> dict[str, object]:
    return {
        "address": "0x0000000000000000000000000000000000000133",
        "topics": [
            ORDER_CREATED_TOPIC0,
            hex(order_id),
            hex(7),
            "0x0000000000000000000000001111111111111111111111111111111111111111",
        ],
        "data": "0x"
        + "00000000000000000000000000000000000000000000000000000000000003e8"
        + "0000000000000000000000000000000000000000000000000000000000000abc",
        "transactionHash": "0xabc123",
        "logIndex": "0x1",
    }


def test_broadcaster_uses_live_receipt_when_available() -> None:
    broadcaster = OnchainBroadcaster(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address="0xadmin",
                to_address="0x0000000000000000000000000000000000000134",
                block_number=777,
                event_id="receipt:0xabc123:777",
                metadata={"logs": [_order_created_log(42)]},
            )
        )
    )

    receipt = broadcaster.broadcast_create_paid_order(write_result=_write_result())

    assert receipt.tx_hash == "0xabc123"
    assert receipt.event_id == "OrderCreated:42:0xabc123"
    assert receipt.block_number == 777
    assert receipt.onchain_order_id == "42"


def test_broadcaster_falls_back_to_deterministic_receipt_without_live_rpc() -> None:
    broadcaster = OnchainBroadcaster(receipt_reader=StubReceiptReader(None))

    receipt = broadcaster.broadcast_create_paid_order(write_result=_write_result())

    assert receipt.tx_hash == "0xabc123"
    assert receipt.event_id.startswith("OrderCreated:")
    assert receipt.onchain_order_id.isdigit()
    assert receipt.block_number >= 1_000_000
