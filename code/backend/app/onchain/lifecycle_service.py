from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.integrations.user_signer_registry import UserSigner, UserSignerRegistry
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.event_decoder import decode_machine_minted_event
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
            receipt = self._receipt_reader.get_receipt(tx_hash)
            if receipt is not None:
                return receipt
            time.sleep(0.25)
        return None


@lru_cache
def get_onchain_lifecycle_service() -> OnchainLifecycleService:
    return OnchainLifecycleService()


def reset_onchain_lifecycle_service_cache() -> None:
    get_onchain_lifecycle_service.cache_clear()
