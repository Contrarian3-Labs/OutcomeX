"""EVM runtime helpers for live polling and ABI decoding."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from app.onchain.adapter import EventDecoder, EventSubscription, RawLog

_EVENT_SIGNATURES: dict[tuple[str, str], str] = {
    ("MachineAssetNFT", "MachineMinted"): "MachineMinted(uint256,address,string)",
    ("MachineAssetNFT", "Transfer"): "Transfer(address,address,uint256)",
    ("OrderBook", "OrderCreated"): "OrderCreated(uint256,uint256,address,uint256,address)",
    ("OrderBook", "OrderClassified"): "OrderClassified(uint256,bool,bool)",
    ("OrderBook", "OrderPaid"): "OrderPaid(uint256,uint256,uint256)",
    ("OrderBook", "PreviewReady"): "PreviewReady(uint256,uint256,bool)",
    ("OrderBook", "OrderSettled"): "OrderSettled(uint256,uint256,uint8,uint256,uint256,uint256,bool)",
    (
        "SettlementController",
        "Settled",
    ): "Settled(uint256,uint256,uint8,address,address,uint256,uint256,uint256,uint256,bool)",
    ("SettlementController", "RefundClaimed"): "RefundClaimed(address,uint256)",
    ("SettlementController", "PlatformRevenueClaimed"): "PlatformRevenueClaimed(address,uint256)",
    ("RevenueVault", "RevenueAccrued"): "RevenueAccrued(uint256,uint256,address,uint256,bool)",
    ("RevenueVault", "RevenueClaimed"): "RevenueClaimed(uint256,address,uint256)",
    ("PWRToken", "Transfer"): "Transfer(address,address,uint256)",
}

_EVENT_TOPIC0_BY_SIGNATURE: dict[str, str] = {
    "MachineMinted(uint256,address,string)": "0x1dc7a4274503103baffb2f8cf9ab4b87fd7e3751dd8471358351d3bc324e8758",
    "Transfer(address,address,uint256)": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "OrderCreated(uint256,uint256,address,uint256,address)": "0x10a337bf06bb798704a2c57575959ef9198b9a7c57e24ea27f8e728a620d272d",
    "OrderClassified(uint256,bool,bool)": "0x0214adacbe9e5548bb02fb8f97fce31e344b48dc0868d46d804fb0a07dda244d",
    "OrderPaid(uint256,uint256,uint256)": "0xe575062745448617c648dfd40a6400faddf8c5a9ab45c59b0f8aaeb3135b23da",
    "PreviewReady(uint256,uint256,bool)": "0x153aad31fe4dbe2c67053c077f5e7bf2749b197d34d3e36eaf9f2ee2326cc183",
    "OrderSettled(uint256,uint256,uint8,uint256,uint256,uint256,bool)": "0xe1f40c85f9bbf31b5c51006d63d7e0749be83f9edc49e1290159f1ceac9a48c7",
    "Settled(uint256,uint256,uint8,address,address,uint256,uint256,uint256,uint256,bool)": "0xcb5ad5c3a251c59218f10948d147c1c5e275fa0e9f397ee51162ad3160a5f32a",
    "RevenueAccrued(uint256,uint256,address,uint256,bool)": "0x133741d3b1dc341b0ad5d327217a1c25c679f1a3af5000b388b703ffdc63fbc1",
    "RevenueClaimed(uint256,address,uint256)": "0xb9e8470097faa00e83252475f2ee4b69007b0bb2405268ebec248676998a21b3",
    "RefundClaimed(address,uint256)": "0x358fe4192934d3bf28ae181feda1f4bd08ca67f5e2fad55582cce5eb67304ae9",
    "PlatformRevenueClaimed(address,uint256)": "0x62e7c8b581e0a5022d2d514d7e366fe69f77d8b356206261bd2058c838d84a8f",
}

_CONTRACT_ENV_KEYS: dict[str, str] = {
    "MachineAssetNFT": "OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS",
    "OrderBook": "OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS",
    "SettlementController": "OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS",
    "RevenueVault": "OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS",
    "PWRToken": "OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS",
}

_EVENT_ABIS: dict[tuple[str, str], Mapping[str, Any]] = {
    (
        "MachineAssetNFT",
        "MachineMinted",
    ): {
        "type": "event",
        "name": "MachineMinted",
        "inputs": [
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "owner", "type": "address"},
            {"indexed": False, "name": "tokenURI", "type": "string"},
        ],
        "anonymous": False,
    },
    (
        "MachineAssetNFT",
        "Transfer",
    ): {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "OrderBook",
        "OrderCreated",
    ): {
        "type": "event",
        "name": "OrderCreated",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "buyer", "type": "address"},
            {"indexed": False, "name": "grossAmount", "type": "uint256"},
            {"indexed": False, "name": "settlementBeneficiary", "type": "address"},
        ],
        "anonymous": False,
    },
    (
        "OrderBook",
        "OrderClassified",
    ): {
        "type": "event",
        "name": "OrderClassified",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": False, "name": "dividendEligible", "type": "bool"},
            {"indexed": False, "name": "refundFailedOrNoValidPreviewAuthorized", "type": "bool"},
        ],
        "anonymous": False,
    },
    (
        "OrderBook",
        "OrderPaid",
    ): {
        "type": "event",
        "name": "OrderPaid",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": False, "name": "grossAmount", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "OrderBook",
        "PreviewReady",
    ): {
        "type": "event",
        "name": "PreviewReady",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": False, "name": "validPreview", "type": "bool"},
        ],
        "anonymous": False,
    },
    (
        "OrderBook",
        "OrderSettled",
    ): {
        "type": "event",
        "name": "OrderSettled",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": False, "name": "kind", "type": "uint8"},
            {"indexed": False, "name": "refundToBuyer", "type": "uint256"},
            {"indexed": False, "name": "platformShare", "type": "uint256"},
            {"indexed": False, "name": "machineShare", "type": "uint256"},
            {"indexed": False, "name": "dividendEligible", "type": "bool"},
        ],
        "anonymous": False,
    },
    (
        "SettlementController",
        "Settled",
    ): {
        "type": "event",
        "name": "Settled",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": False, "name": "kind", "type": "uint8"},
            {"indexed": False, "name": "buyer", "type": "address"},
            {"indexed": False, "name": "settlementBeneficiary", "type": "address"},
            {"indexed": False, "name": "grossAmount", "type": "uint256"},
            {"indexed": False, "name": "refundToBuyer", "type": "uint256"},
            {"indexed": False, "name": "platformShare", "type": "uint256"},
            {"indexed": False, "name": "machineShare", "type": "uint256"},
            {"indexed": False, "name": "dividendEligible", "type": "bool"},
        ],
        "anonymous": False,
    },
    (
        "SettlementController",
        "RefundClaimed",
    ): {
        "type": "event",
        "name": "RefundClaimed",
        "inputs": [
            {"indexed": True, "name": "buyer", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "SettlementController",
        "PlatformRevenueClaimed",
    ): {
        "type": "event",
        "name": "PlatformRevenueClaimed",
        "inputs": [
            {"indexed": True, "name": "treasury", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "RevenueVault",
        "RevenueAccrued",
    ): {
        "type": "event",
        "name": "RevenueAccrued",
        "inputs": [
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineOwner", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
            {"indexed": False, "name": "dividendEligible", "type": "bool"},
        ],
        "anonymous": False,
    },
    (
        "RevenueVault",
        "RevenueClaimed",
    ): {
        "type": "event",
        "name": "RevenueClaimed",
        "inputs": [
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "machineOwner", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "PWRToken",
        "Transfer",
    ): {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "anonymous": False,
    },
}


@dataclass(frozen=True)
class EvmIndexerRuntimeConfig:
    chain_id: int
    rpc_url: str
    poll_seconds: float
    confirmation_depth: int
    bootstrap_block: int
    max_block_span: int


def load_runtime_config_from_env() -> EvmIndexerRuntimeConfig:
    chain_id = int(os.getenv("OUTCOMEX_ONCHAIN_CHAIN_ID", "133"))
    rpc_url = os.getenv("OUTCOMEX_ONCHAIN_RPC_URL", "").strip()
    poll_seconds = float(os.getenv("OUTCOMEX_ONCHAIN_INDEXER_POLL_SECONDS", "2"))
    confirmation_depth = int(os.getenv("OUTCOMEX_ONCHAIN_INDEXER_CONFIRMATION_DEPTH", "0"))
    bootstrap_block = int(os.getenv("OUTCOMEX_ONCHAIN_INDEXER_BOOTSTRAP_BLOCK", "0"))
    max_block_span = int(os.getenv("OUTCOMEX_ONCHAIN_INDEXER_MAX_BLOCK_SPAN", "2000"))
    return EvmIndexerRuntimeConfig(
        chain_id=chain_id,
        rpc_url=rpc_url,
        poll_seconds=poll_seconds,
        confirmation_depth=confirmation_depth,
        bootstrap_block=bootstrap_block,
        max_block_span=max_block_span,
    )


def build_subscriptions_from_env() -> tuple[EventSubscription, ...]:
    subscriptions: list[EventSubscription] = []
    for contract_name, env_key in _CONTRACT_ENV_KEYS.items():
        address = _normalize_address(os.getenv(env_key, "").strip())
        if not address:
            continue
        for (abi_contract, event_name), signature in _EVENT_SIGNATURES.items():
            if abi_contract != contract_name:
                continue
            subscriptions.append(
                EventSubscription(
                    contract_name=contract_name,
                    contract_address=address,
                    event_name=event_name,
                    topic0=_event_topic0(signature),
                )
            )
    return tuple(subscriptions)


class Web3AbiEventDecoder(EventDecoder):
    """Decode logs with web3 ABI parser."""

    def __init__(self) -> None:
        from web3 import Web3

        self._codec = Web3().codec

    def decode(self, *, subscription: EventSubscription, raw_log: RawLog) -> Mapping[str, Any]:
        from hexbytes import HexBytes
        from web3 import Web3
        from web3._utils.events import get_event_data

        event_abi = _EVENT_ABIS[(subscription.contract_name, subscription.event_name)]
        log = {
            "address": Web3.to_checksum_address(raw_log.contract_address),
            "topics": [HexBytes(topic) for topic in raw_log.topics],
            "data": HexBytes(raw_log.data),
            "blockNumber": raw_log.block_number,
            "transactionIndex": 0,
            "transactionHash": HexBytes(raw_log.transaction_hash),
            "blockHash": HexBytes(raw_log.block_hash),
            "logIndex": raw_log.log_index,
        }
        decoded = get_event_data(self._codec, event_abi, log)
        return dict(decoded["args"])


def _normalize_address(value: str) -> str:
    if not value:
        return ""
    try:
        from web3 import Web3
    except ModuleNotFoundError:
        lowered = value.lower()
        if lowered.startswith("0x") and len(lowered) == 42:
            return lowered
        return ""
    try:
        return Web3.to_checksum_address(value)
    except ValueError:
        return ""


def _event_topic0(signature: str) -> str:
    return _EVENT_TOPIC0_BY_SIGNATURE[signature]
