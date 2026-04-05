from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class ChainReceipt:
    tx_hash: str
    status: int
    from_address: str | None
    to_address: str | None
    block_number: int
    event_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ReceiptReader(Protocol):
    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        ...


class NullReceiptReader:
    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        return None


class JsonRpcReceiptReader:
    def __init__(self, *, rpc_url: str, timeout_seconds: float = 10.0) -> None:
        self._rpc_url = rpc_url
        self._timeout_seconds = timeout_seconds

    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
        }
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(self._rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result")
        if result is None:
            return None

        status = int(result.get("status", "0x0"), 16)
        block_number = int(result.get("blockNumber", "0x0"), 16)
        tx_hash_normalized = str(result.get("transactionHash", tx_hash)).lower()
        event_id = f"receipt:{tx_hash_normalized}:{block_number}"
        return ChainReceipt(
            tx_hash=tx_hash_normalized,
            status=status,
            from_address=_normalize_address(result.get("from")),
            to_address=_normalize_address(result.get("to")),
            block_number=block_number,
            event_id=event_id,
            metadata={"logs": list(result.get("logs", []))},
        )


def _normalize_address(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).lower()


@lru_cache
def get_receipt_reader() -> ReceiptReader:
    settings = get_settings()
    if settings.onchain_rpc_url:
        return JsonRpcReceiptReader(rpc_url=settings.onchain_rpc_url)
    return NullReceiptReader()
