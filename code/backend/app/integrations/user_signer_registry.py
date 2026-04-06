from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class UserSigner:
    user_id: str
    wallet_address: str
    private_key: str


class UserSignerRegistry:
    """Resolve backend user identities to signer wallets and private keys."""

    def __init__(self, mapping: dict[str, UserSigner] | None = None) -> None:
        self._signers = dict(mapping or {})
        self._wallet_to_user = {
            signer.wallet_address.lower(): signer.user_id
            for signer in self._signers.values()
        }

    @classmethod
    def from_json(cls, mapping_json: str | None) -> "UserSignerRegistry":
        if not mapping_json:
            return cls()
        parsed = json.loads(mapping_json)
        if not isinstance(parsed, dict):
            raise ValueError("user_signer_private_keys_json_must_be_object")

        signers: dict[str, UserSigner] = {}
        for raw_user_id, raw_private_key in parsed.items():
            user_id = str(raw_user_id)
            private_key = cls._normalize_private_key(str(raw_private_key))
            wallet_address = cls._derive_wallet_address(private_key)
            signers[user_id] = UserSigner(
                user_id=user_id,
                wallet_address=wallet_address,
                private_key=private_key,
            )
        return cls(signers)

    def signer_for_user(self, user_id: str) -> UserSigner | None:
        return self._signers.get(user_id)

    def resolve_private_key(self, user_id: str) -> str | None:
        signer = self.signer_for_user(user_id)
        return signer.private_key if signer is not None else None

    def resolve_wallet(self, user_id: str) -> str | None:
        signer = self.signer_for_user(user_id)
        return signer.wallet_address if signer is not None else None

    def resolve_user_id(self, wallet_address: str) -> str | None:
        return self._wallet_to_user.get(str(wallet_address).lower())

    @staticmethod
    def _normalize_private_key(value: str) -> str:
        normalized = value.lower()
        if not normalized.startswith("0x"):
            normalized = f"0x{normalized}"
        hex_part = normalized[2:]
        if len(hex_part) != 64:
            raise ValueError("invalid_private_key")
        try:
            int(hex_part, 16)
        except ValueError as exc:
            raise ValueError("invalid_private_key") from exc
        return normalized

    @staticmethod
    def _derive_wallet_address(private_key: str) -> str:
        from eth_account import Account

        return str(Account.from_key(private_key).address).lower()
