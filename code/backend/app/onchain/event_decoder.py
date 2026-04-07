from __future__ import annotations

from typing import Any

from app.onchain.receipts import ChainReceipt

ORDER_CREATED_TOPIC0 = "0x10a337bf06bb798704a2c57575959ef9198b9a7c57e24ea27f8e728a620d272d"
ORDER_PAYMENT_RECEIVED_TOPIC0 = "0x108f7e2a0bbfd2535074381616daaa6b78b30921bd6a155acec03ed98ad5792f"
MACHINE_MINTED_TOPIC0 = "0x1dc7a4274503103baffb2f8cf9ab4b87fd7e3751dd8471358351d3bc324e8758"


def _decode_uint256(value: str) -> int:
    return int(str(value), 16)


def _decode_topic_address(topic: str) -> str:
    normalized = str(topic).lower().removeprefix("0x")
    return f"0x{normalized[-40:]}"


def _decode_data_word(data: str, index: int) -> str | None:
    normalized = str(data).lower().removeprefix("0x")
    start = index * 64
    end = start + 64
    if len(normalized) < end:
        return None
    return normalized[start:end]


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
        if len(topics) < 4:
            continue
        order_id = _decode_uint256(topics[1])
        machine_id = _decode_uint256(topics[2])
        buyer = _decode_topic_address(topics[3])
        gross_amount_word = _decode_data_word(str(log.get("data", "")), 0)
        settlement_beneficiary_word = _decode_data_word(str(log.get("data", "")), 1)
        return {
            "order_id": str(order_id),
            "machine_id": str(machine_id),
            "buyer": buyer,
            "gross_amount": _decode_uint256(gross_amount_word) if gross_amount_word is not None else None,
            "settlement_beneficiary": (
                f"0x{settlement_beneficiary_word[-40:]}" if settlement_beneficiary_word is not None else None
            ),
            "transaction_hash": str(log.get("transactionHash", receipt.tx_hash)).lower(),
            "log_index": (
                int(str(log.get("logIndex", "0x0")), 16)
                if isinstance(log.get("logIndex"), str)
                else int(log.get("logIndex", 0))
            ),
        }

    return None


def decode_order_payment_received_event(
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
        if not topics or topics[0] != ORDER_PAYMENT_RECEIVED_TOPIC0:
            continue
        if len(topics) < 4:
            continue
        amount_word = _decode_data_word(str(log.get("data", "")), 0)
        payment_source_word = _decode_data_word(str(log.get("data", "")), 1)
        return {
            "order_id": str(_decode_uint256(topics[1])),
            "payer": _decode_topic_address(topics[2]),
            "token": _decode_topic_address(topics[3]),
            "amount": _decode_uint256(amount_word) if amount_word is not None else None,
            "payment_source": f"0x{payment_source_word}" if payment_source_word is not None else None,
            "transaction_hash": str(log.get("transactionHash", receipt.tx_hash)).lower(),
            "log_index": (
                int(str(log.get("logIndex", "0x0")), 16)
                if isinstance(log.get("logIndex"), str)
                else int(log.get("logIndex", 0))
            ),
        }

    return None


def decode_machine_minted_event(
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
        if not topics or topics[0] != MACHINE_MINTED_TOPIC0:
            continue
        if len(topics) < 2:
            continue
        machine_id = int(topics[1], 16)
        return {
            "machine_id": str(machine_id),
            "transaction_hash": str(log.get("transactionHash", receipt.tx_hash)).lower(),
            "log_index": int(str(log.get("logIndex", "0x0")), 16) if isinstance(log.get("logIndex"), str) else int(log.get("logIndex", 0)),
        }

    return None
