from __future__ import annotations

import json
import re
from typing import Mapping


_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class BuyerAddressResolver:
    """Resolve backend user identities to EVM buyer addresses and back."""

    def __init__(self, mapping: Mapping[str, str] | None = None) -> None:
        raw_mapping = dict(mapping or {})
        self._user_to_wallet = {
            user_id: self._normalize_wallet(wallet_address)
            for user_id, wallet_address in raw_mapping.items()
        }
        self._wallet_to_user = {wallet: user_id for user_id, wallet in self._user_to_wallet.items()}

    @classmethod
    def from_json(cls, mapping_json: str | None) -> "BuyerAddressResolver":
        if not mapping_json:
            return cls()
        parsed = json.loads(mapping_json)
        if not isinstance(parsed, dict):
            raise ValueError("buyer_wallet_map_json_must_be_object")
        return cls({str(user_id): str(wallet_address) for user_id, wallet_address in parsed.items()})

    def resolve_wallet(self, user_id: str) -> str | None:
        direct_wallet = self._try_normalize_wallet(user_id)
        if direct_wallet is not None:
            return direct_wallet
        return self._user_to_wallet.get(user_id)

    def resolve_user_id(self, wallet_address: str) -> str | None:
        normalized = self._normalize_wallet(wallet_address)
        return self._wallet_to_user.get(normalized) or normalized

    def canonicalize_user_id(self, user_id_or_wallet: str) -> str:
        direct_wallet = self._try_normalize_wallet(user_id_or_wallet)
        if direct_wallet is None:
            return user_id_or_wallet
        return self.resolve_user_id(direct_wallet) or direct_wallet

    @staticmethod
    def _normalize_wallet(wallet_address: str) -> str:
        if not _EVM_ADDRESS_RE.match(wallet_address):
            raise ValueError(f"invalid_evm_address:{wallet_address}")
        return wallet_address.lower()

    @staticmethod
    def _try_normalize_wallet(wallet_address: str | None) -> str | None:
        if not wallet_address:
            return None
        try:
            return BuyerAddressResolver._normalize_wallet(wallet_address)
        except ValueError:
            return None
