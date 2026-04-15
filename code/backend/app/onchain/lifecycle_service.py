from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.integrations.user_signer_registry import UserSigner, UserSignerRegistry
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.event_decoder import MACHINE_MINTED_TOPIC0, decode_machine_minted_event
from app.onchain.order_writer import OrderWriteResult, OrderWriter
from app.onchain.receipts import ChainReceipt, JsonRpcReceiptReader
from app.onchain.tx_sender import PythonTransactionSender


@dataclass(frozen=True)
class BroadcastReceipt:
    tx_hash: str
    receipt: ChainReceipt | None


@dataclass(frozen=True)
class MintedMachineReceipt(BroadcastReceipt):
    onchain_machine_id: str | None


class OnchainLifecycleService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        contracts_registry: ContractsRegistry | None = None,
        order_writer: OrderWriter | None = None,
        buyer_address_resolver: BuyerAddressResolver | None = None,
        user_signer_registry: UserSignerRegistry | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._contracts_registry = contracts_registry or ContractsRegistry(settings=self._settings)
        self._order_writer = order_writer or OrderWriter(self._contracts_registry)
        self._buyer_address_resolver = buyer_address_resolver or BuyerAddressResolver.from_json(
            self._settings.buyer_wallet_map_json
        )
        self._user_signer_registry = user_signer_registry or self._build_user_signer_registry()
        self._receipt_reader = JsonRpcReceiptReader(
            rpc_url=self._settings.onchain_rpc_url,
            timeout_seconds=self._settings.onchain_receipt_timeout_seconds,
        )

    def enabled(self) -> bool:
        return bool(self._settings.onchain_rpc_url.strip())

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str) -> MintedMachineReceipt:
        owner_wallet = self._buyer_address_resolver.resolve_wallet(owner_user_id)
        if owner_wallet is None:
            raise RuntimeError("owner_wallet_unresolved")
        write_result = self._order_writer.mint_machine(
            owner_wallet_address=owner_wallet,
            token_uri=token_uri,
            owner_user_id=owner_user_id,
        )
        receipt = self._send_as_admin(write_result)
        machine_mint = (
            decode_machine_minted_event(
                receipt=receipt.receipt,
                contract_address=self._contracts_registry.machine_asset().contract_address,
            )
            if receipt.receipt is not None
            else None
        )
        return MintedMachineReceipt(
            tx_hash=receipt.tx_hash,
            receipt=receipt.receipt,
            onchain_machine_id=machine_mint["machine_id"] if machine_mint is not None else None,
        )

    def find_minted_machine_by_token_uri(self, *, token_uri: str, from_block: int | None = None) -> str | None:
        if not self.enabled():
            return None

        logs = self._fetch_machine_minted_logs(from_block=from_block)
        for log in reversed(logs):
            decoded = self._decode_machine_minted_log(log)
            if decoded.get("token_uri") != token_uri:
                continue
            return decoded.get("machine_id")
        return None

    def send_as_user(self, *, user_id: str, write_result: OrderWriteResult) -> BroadcastReceipt:
        expected_wallet = self._buyer_address_resolver.resolve_wallet(user_id)
        signer = self._user_signer_registry.signer_for_user(user_id)
        if signer is not None and expected_wallet and signer.wallet_address != expected_wallet.lower():
            signer = self._user_signer_registry.signer_for_wallet(expected_wallet)
        if signer is None:
            if expected_wallet:
                raise RuntimeError(f"signer_wallet_unresolved:{user_id}:{expected_wallet.lower()}")
            raise RuntimeError(f"signer_missing:{user_id}")
        if expected_wallet and signer.wallet_address != expected_wallet.lower():
            raise RuntimeError(
                f"signer_wallet_mismatch:{user_id}:expected={expected_wallet.lower()}:actual={signer.wallet_address}"
            )
        return self._send(write_result, private_key=signer.private_key)

    def send_as_admin(self, *, write_result: OrderWriteResult) -> BroadcastReceipt:
        return self._send_as_admin(write_result)

    def send_as_treasury(self, *, write_result: OrderWriteResult) -> BroadcastReceipt:
        private_key = self._settings.onchain_platform_treasury_private_key.strip()
        if not private_key:
            raise RuntimeError("treasury_private_key_missing")
        return self._send(write_result, private_key=private_key)

    def _build_user_signer_registry(self) -> UserSignerRegistry:
        registry = UserSignerRegistry.from_json(self._settings.user_signer_private_keys_json)
        for private_key in (
            self._settings.onchain_buyer_private_key,
            self._settings.onchain_machine_owner_private_key,
        ):
            signer = self._signer_from_private_key(private_key)
            if signer is None:
                continue
            user_id = self._buyer_address_resolver.resolve_user_id(signer.wallet_address)
            if user_id is None:
                continue
            registry = registry.with_signer(
                UserSigner(
                    user_id=user_id,
                    wallet_address=signer.wallet_address,
                    private_key=signer.private_key,
                )
            )
        return registry

    @staticmethod
    def _signer_from_private_key(private_key: str) -> UserSigner | None:
        normalized = private_key.strip()
        if not normalized:
            return None
        wallet_address = UserSignerRegistry._derive_wallet_address(
            UserSignerRegistry._normalize_private_key(normalized)
        )
        normalized_key = UserSignerRegistry._normalize_private_key(normalized)
        return UserSigner(user_id=wallet_address, wallet_address=wallet_address, private_key=normalized_key)

    def _fetch_machine_minted_logs(self, *, from_block: int | None = None) -> list[dict[str, Any]]:
        start_block = max(0, from_block if from_block is not None else self._settings.onchain_indexer_bootstrap_block)
        max_span = max(1, int(self._settings.onchain_indexer_max_block_span))
        try:
            with httpx.Client(timeout=max(1.0, self._settings.onchain_receipt_timeout_seconds)) as client:
                latest_block_response = client.post(
                    self._settings.onchain_rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                )
                latest_block_response.raise_for_status()
                latest_block = int(str(latest_block_response.json().get("result", "0x0")), 16)

                results: list[dict[str, Any]] = []
                block_cursor = start_block
                while block_cursor <= latest_block:
                    batch_to_block = min(block_cursor + max_span - 1, latest_block)
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_getLogs",
                        "params": [
                            {
                                "fromBlock": hex(block_cursor),
                                "toBlock": hex(batch_to_block),
                                "address": self._contracts_registry.machine_asset().contract_address,
                                "topics": [MACHINE_MINTED_TOPIC0],
                            }
                        ],
                    }
                    response = client.post(self._settings.onchain_rpc_url, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    result = body.get("result")
                    if not isinstance(result, list):
                        raise RuntimeError("machine_minted_log_fetch_invalid_payload")
                    results.extend(item for item in result if isinstance(item, dict))
                    block_cursor = batch_to_block + 1
        except Exception as exc:  # pragma: no cover - network failures vary by environment
            raise RuntimeError("machine_minted_log_fetch_failed") from exc
        return results

    @staticmethod
    def _decode_machine_minted_log(log: dict[str, Any]) -> dict[str, str]:
        topics = list(log.get("topics", []))
        if len(topics) < 2:
            raise RuntimeError("machine_minted_log_decode_failed:missing_topics")
        try:
            machine_id = str(int(str(topics[1]), 16))
            token_uri = OnchainLifecycleService._decode_dynamic_string(str(log.get("data", "")))
        except Exception as exc:
            raise RuntimeError("machine_minted_log_decode_failed") from exc
        return {
            "machine_id": machine_id,
            "token_uri": token_uri,
        }

    @staticmethod
    def _decode_dynamic_string(data: str) -> str:
        normalized = str(data).lower().removeprefix("0x")
        if len(normalized) < 128:
            raise ValueError("dynamic_string_missing_head")
        offset = int(normalized[0:64], 16)
        length_offset = offset * 2
        if len(normalized) < length_offset + 64:
            raise ValueError("dynamic_string_missing_length")
        length = int(normalized[length_offset : length_offset + 64], 16)
        value_start = length_offset + 64
        value_end = value_start + (length * 2)
        if len(normalized) < value_end:
            raise ValueError("dynamic_string_missing_value")
        return bytes.fromhex(normalized[value_start:value_end]).decode("utf-8")

    def _send_as_admin(self, write_result: OrderWriteResult) -> BroadcastReceipt:
        private_key = self._settings.onchain_broadcaster_private_key.strip()
        if not private_key:
            raise RuntimeError("admin_private_key_missing")
        return self._send(write_result, private_key=private_key)

    def _send(self, write_result: OrderWriteResult, *, private_key: str) -> BroadcastReceipt:
        sender = PythonTransactionSender(
            rpc_url=self._settings.onchain_rpc_url,
            private_key=private_key,
            contracts_registry=self._contracts_registry,
        )
        sent = sender.send(write_result)
        receipt = self._wait_for_receipt(sent.tx_hash)
        if receipt is None:
            raise RuntimeError(f"transaction_receipt_missing:{sent.tx_hash}")
        if receipt.status != 1:
            raise RuntimeError(f"transaction_failed:{sent.tx_hash}")
        return BroadcastReceipt(tx_hash=sent.tx_hash, receipt=receipt)

    def _wait_for_receipt(self, tx_hash: str) -> ChainReceipt | None:
        deadline = time.time() + max(0.1, self._settings.onchain_tx_timeout_seconds)
        while time.time() < deadline:
            try:
                receipt = self._receipt_reader.get_receipt(tx_hash)
            except Exception:
                time.sleep(0.25)
                continue
            if receipt is not None:
                return receipt
            time.sleep(0.25)
        return None


@lru_cache
def get_onchain_lifecycle_service() -> OnchainLifecycleService:
    return OnchainLifecycleService()


def reset_onchain_lifecycle_service_cache() -> None:
    get_onchain_lifecycle_service.cache_clear()
