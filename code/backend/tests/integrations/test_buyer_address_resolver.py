import pytest

from app.integrations.buyer_address_resolver import BuyerAddressResolver


def test_resolver_maps_user_ids_to_wallets_and_back() -> None:
    resolver = BuyerAddressResolver.from_json(
        '{"user-1":"0x1111111111111111111111111111111111111111","user-2":"0x2222222222222222222222222222222222222222"}'
    )

    assert resolver.resolve_wallet("user-1") == "0x1111111111111111111111111111111111111111"
    assert resolver.resolve_user_id("0x2222222222222222222222222222222222222222") == "user-2"


def test_resolver_rejects_invalid_wallet_addresses() -> None:
    with pytest.raises(ValueError, match="invalid_evm_address"):
        BuyerAddressResolver.from_json('{"user-1":"not-an-address"}')
