"""EVM runtime helpers for live polling and ABI decoding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.core.config import Settings, get_settings
from app.onchain.adapter import EventDecoder, EventSubscription, RawLog

_EVENT_SIGNATURES: dict[tuple[str, str], str] = {
    ("MachineAssetNFT", "MachineMinted"): "MachineMinted(uint256,address,string)",
    ("MachineAssetNFT", "Transfer"): "Transfer(address,address,uint256)",
    (
        "MachineMarketplace",
        "ListingCreated",
    ): "ListingCreated(uint256,uint256,address,address,uint256,uint64)",
    ("MachineMarketplace", "ListingCancelled"): "ListingCancelled(uint256,uint256,address)",
    (
        "MachineMarketplace",
        "ListingPurchased",
    ): "ListingPurchased(uint256,uint256,address,address,address,uint256)",
    ("OrderBook", "OrderCreated"): "OrderCreated(uint256,uint256,address,uint256,address)",
    ("OrderBook", "OrderClassified"): "OrderClassified(uint256,bool,bool)",
    ("OrderBook", "OrderCancelled"): "OrderCancelled(uint256,uint256,address,uint64,bool)",
    ("OrderBook", "PreviewReady"): "PreviewReady(uint256,uint256,bool)",
    ("OrderBook", "OrderSettled"): "OrderSettled(uint256,uint256,uint8,uint256,uint256,uint256,bool)",
    (
        "OrderPaymentRouter",
        "PaymentFinalized",
    ): "PaymentFinalized(uint256,uint256,address,address,address,uint256,bytes32,address,bool,bool)",
    (
        "SettlementController",
        "Settled",
    ): "Settled(uint256,uint256,uint8,address,address,uint256,uint256,uint256,uint256,bool)",
    (
        "SettlementController",
        "RefundClaimedDetailed",
    ): "RefundClaimedDetailed(address,address,uint256,uint256)",
    (
        "SettlementController",
        "PlatformRevenueClaimedDetailed",
    ): "PlatformRevenueClaimedDetailed(address,address,uint256,uint256)",
    ("RevenueVault", "RevenueAccrued"): "RevenueAccrued(uint256,uint256,address,uint256,bool)",
    (
        "RevenueVault",
        "MachineRevenueClaimedDetailed",
    ): "MachineRevenueClaimedDetailed(uint256,address,uint256,uint256,uint256)",
    ("PWRToken", "Transfer"): "Transfer(address,address,uint256)",
}

_EVENT_TOPIC0_BY_SIGNATURE: dict[str, str] = {
    "MachineMinted(uint256,address,string)": "0x1dc7a4274503103baffb2f8cf9ab4b87fd7e3751dd8471358351d3bc324e8758",
    "Transfer(address,address,uint256)": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "ListingCreated(uint256,uint256,address,address,uint256,uint64)": "0x7abd1a4c3d1e5765e811a7e4020f3293a052e114c357b4d06ef2e4d8281c25fb",
    "ListingCancelled(uint256,uint256,address)": "0x5f0be1e06429123bb18a91b02cae2672c631c3d0eef488ee77e1e9870a6e2cb4",
    "ListingPurchased(uint256,uint256,address,address,address,uint256)": "0x909cd07b57d37abc276b9584da754de2d6a91ab6b26b4ce8e70c9702aef31bb4",
    "OrderCreated(uint256,uint256,address,uint256,address)": "0x10a337bf06bb798704a2c57575959ef9198b9a7c57e24ea27f8e728a620d272d",
    "OrderClassified(uint256,bool,bool)": "0x0214adacbe9e5548bb02fb8f97fce31e344b48dc0868d46d804fb0a07dda244d",
    "OrderCancelled(uint256,uint256,address,uint64,bool)": "0x75745ade561ac203c37b0de71a179d5d1342edfca5fc690daefabbb9905ace65",
    "PreviewReady(uint256,uint256,bool)": "0x153aad31fe4dbe2c67053c077f5e7bf2749b197d34d3e36eaf9f2ee2326cc183",
    "OrderSettled(uint256,uint256,uint8,uint256,uint256,uint256,bool)": "0xe1f40c85f9bbf31b5c51006d63d7e0749be83f9edc49e1290159f1ceac9a48c7",
    "PaymentFinalized(uint256,uint256,address,address,address,uint256,bytes32,address,bool,bool)": "0x850b624ea957fa98195ba9402674a7e91432c8c512c4ed1afb7d96d80feab57a",
    "Settled(uint256,uint256,uint8,address,address,uint256,uint256,uint256,uint256,bool)": "0xcb5ad5c3a251c59218f10948d147c1c5e275fa0e9f397ee51162ad3160a5f32a",
    "RevenueAccrued(uint256,uint256,address,uint256,bool)": "0x133741d3b1dc341b0ad5d327217a1c25c679f1a3af5000b388b703ffdc63fbc1",
    "RefundClaimedDetailed(address,address,uint256,uint256)": "0x10587e6af1b0fe52f3da9b47862326ef4ddc900298494f60d0b065f14d846630",
    "PlatformRevenueClaimedDetailed(address,address,uint256,uint256)": "0xff68820b68e706e0f608a3cdbf9bd873e497069a7f1fca80aa1614050230f101",
    "MachineRevenueClaimedDetailed(uint256,address,uint256,uint256,uint256)": "0xebe5911c4f80b9ac15afb5a4eca8e737e84e02be5c310106f58d771d8e1391f9",
}

_CONTRACT_ENV_KEYS: dict[str, str] = {
    "MachineAssetNFT": "OUTCOMEX_ONCHAIN_MACHINE_ASSET_ADDRESS",
    "MachineMarketplace": "OUTCOMEX_ONCHAIN_MACHINE_MARKETPLACE_ADDRESS",
    "OrderBook": "OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS",
    "OrderPaymentRouter": "OUTCOMEX_ONCHAIN_ORDER_PAYMENT_ROUTER_ADDRESS",
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
        "MachineMarketplace",
        "ListingCreated",
    ): {
        "type": "event",
        "name": "ListingCreated",
        "inputs": [
            {"indexed": True, "name": "listingId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "seller", "type": "address"},
            {"indexed": False, "name": "paymentToken", "type": "address"},
            {"indexed": False, "name": "price", "type": "uint256"},
            {"indexed": False, "name": "expiry", "type": "uint64"},
        ],
        "anonymous": False,
    },
    (
        "MachineMarketplace",
        "ListingCancelled",
    ): {
        "type": "event",
        "name": "ListingCancelled",
        "inputs": [
            {"indexed": True, "name": "listingId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "cancelledBy", "type": "address"},
        ],
        "anonymous": False,
    },
    (
        "MachineMarketplace",
        "ListingPurchased",
    ): {
        "type": "event",
        "name": "ListingPurchased",
        "inputs": [
            {"indexed": True, "name": "listingId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "buyer", "type": "address"},
            {"indexed": False, "name": "seller", "type": "address"},
            {"indexed": False, "name": "paymentToken", "type": "address"},
            {"indexed": False, "name": "price", "type": "uint256"},
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
        "OrderCancelled",
    ): {
        "type": "event",
        "name": "OrderCancelled",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "cancelledBy", "type": "address"},
            {"indexed": False, "name": "cancelledAt", "type": "uint64"},
            {"indexed": False, "name": "expired", "type": "bool"},
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
        "OrderPaymentRouter",
        "PaymentFinalized",
    ): {
        "type": "event",
        "name": "PaymentFinalized",
        "inputs": [
            {"indexed": True, "name": "orderId", "type": "uint256"},
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "buyer", "type": "address"},
            {"indexed": False, "name": "payer", "type": "address"},
            {"indexed": False, "name": "paymentToken", "type": "address"},
            {"indexed": False, "name": "grossAmount", "type": "uint256"},
            {"indexed": False, "name": "paymentSource", "type": "bytes32"},
            {"indexed": False, "name": "settlementBeneficiary", "type": "address"},
            {"indexed": False, "name": "dividendEligible", "type": "bool"},
            {"indexed": False, "name": "refundAuthorized", "type": "bool"},
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
        "RefundClaimedDetailed",
    ): {
        "type": "event",
        "name": "RefundClaimedDetailed",
        "inputs": [
            {"indexed": True, "name": "buyer", "type": "address"},
            {"indexed": True, "name": "token", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
            {"indexed": False, "name": "remainingRefundableAfter", "type": "uint256"},
        ],
        "anonymous": False,
    },
    (
        "SettlementController",
        "PlatformRevenueClaimedDetailed",
    ): {
        "type": "event",
        "name": "PlatformRevenueClaimedDetailed",
        "inputs": [
            {"indexed": True, "name": "treasury", "type": "address"},
            {"indexed": True, "name": "token", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
            {"indexed": False, "name": "remainingPlatformAccruedAfter", "type": "uint256"},
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
        "MachineRevenueClaimedDetailed",
    ): {
        "type": "event",
        "name": "MachineRevenueClaimedDetailed",
        "inputs": [
            {"indexed": True, "name": "machineId", "type": "uint256"},
            {"indexed": True, "name": "machineOwner", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
            {"indexed": False, "name": "remainingClaimableForMachineOwnerAfter", "type": "uint256"},
            {"indexed": False, "name": "remainingUnsettledRevenueByMachineAfter", "type": "uint256"},
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


def load_runtime_config(settings: Settings | None = None) -> EvmIndexerRuntimeConfig:
    resolved = settings or get_settings()
    return EvmIndexerRuntimeConfig(
        chain_id=resolved.onchain_chain_id,
        rpc_url=resolved.onchain_rpc_url.strip(),
        poll_seconds=resolved.onchain_indexer_poll_seconds,
        confirmation_depth=resolved.onchain_indexer_confirmation_depth,
        bootstrap_block=resolved.onchain_indexer_bootstrap_block,
        max_block_span=resolved.onchain_indexer_max_block_span,
    )


def load_runtime_config_from_env() -> EvmIndexerRuntimeConfig:
    return load_runtime_config()


def build_subscriptions(settings: Settings | None = None) -> tuple[EventSubscription, ...]:
    resolved = settings or get_settings()
    configured_addresses = {
        "MachineAssetNFT": resolved.onchain_machine_asset_address,
        "MachineMarketplace": resolved.onchain_machine_marketplace_address,
        "OrderBook": resolved.onchain_order_book_address,
        "OrderPaymentRouter": resolved.onchain_order_payment_router_address,
        "SettlementController": resolved.onchain_settlement_controller_address,
        "RevenueVault": resolved.onchain_revenue_vault_address,
        "PWRToken": resolved.onchain_pwr_token_address,
    }
    subscriptions: list[EventSubscription] = []
    for contract_name in _CONTRACT_ENV_KEYS:
        address = _normalize_address(str(configured_addresses.get(contract_name, "")).strip())
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


def build_subscriptions_from_env() -> tuple[EventSubscription, ...]:
    return build_subscriptions()


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
