from __future__ import annotations

from typing import Any

from app.onchain.receipts import ChainReceipt

ORDER_CREATED_TOPIC0 = "0x10a337bf06bb798704a2c57575959ef9198b9a7c57e24ea27f8e728a620d272d"


def decode_order_created_event(
    *,
    receipt: ChainReceipt,
    contract_address: str,
) -> dict[str, Any] | None:
    logs = list(receipt.metadata.get("logs", []))
    expected_address = contract_address.lower()

    for log in logs:
        log_address = str(log.get("address", "")).lower()
        topics = [str(topic).lower() for topic in log.get("topics", [])]
        if log_address != expected_address:
            continue
        if not topics or topics[0] != ORDER_CREATED_TOPIC0:
            continue
        if len(topics) < 2:
            continue
        order_id = int(topics[1], 16)
        return {
            "order_id": str(order_id),
            "transaction_hash": str(log.get("transactionHash", receipt.tx_hash)).lower(),
            "log_index": int(str(log.get("logIndex", "0x0")), 16) if isinstance(log.get("logIndex"), str) else int(log.get("logIndex", 0)),
        }

    return None
