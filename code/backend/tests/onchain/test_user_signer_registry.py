import pytest

from app.integrations.user_signer_registry import UserSignerRegistry


ALICE_KEY = "0x59c6995e998f97a5a0044966f094538e5d7d9ab1af0b8c1a58ddc3e39fb8bcbf"
BOB_KEY = "0x8b3a350cf5c34c9194ca3a545d8eb4a3e6a6d9f3c0d5857a1f5edecf66da68c1"


def test_registry_derives_wallets_from_private_keys() -> None:
    registry = UserSignerRegistry.from_json(
        '{"alice":"%s","bob":"%s"}' % (ALICE_KEY, BOB_KEY)
    )

    alice = registry.signer_for_user("alice")
    bob = registry.signer_for_user("bob")

    assert alice is not None
    assert bob is not None
    assert alice.wallet_address.startswith("0x")
    assert bob.wallet_address.startswith("0x")
    assert alice.wallet_address != bob.wallet_address
    assert registry.resolve_private_key("alice") == ALICE_KEY
    assert registry.resolve_user_id(alice.wallet_address) == "alice"


def test_registry_rejects_invalid_private_key() -> None:
    with pytest.raises(ValueError, match="invalid_private_key"):
        UserSignerRegistry.from_json('{"alice":"not-a-key"}')
