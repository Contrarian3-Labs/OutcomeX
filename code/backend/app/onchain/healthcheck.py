from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.integrations.buyer_address_resolver import BuyerAddressResolver
from app.integrations.user_signer_registry import UserSignerRegistry
from app.onchain.tx_sender import JsonRpcClient


@dataclass(frozen=True)
class OnchainHealthReport:
    healthy: bool
    rpc_reachable: bool
    chain_id_expected: int
    chain_id_actual: int | None
    contracts: dict[str, dict[str, Any]]
    signers: dict[str, dict[str, Any]]
    identity_mappings: list[dict[str, Any]]
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OnchainHealthChecker:
    def __init__(self, *, settings: Settings | None = None, rpc_client: JsonRpcClient | Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._rpc_client = rpc_client

    def run(self) -> OnchainHealthReport:
        errors: list[str] = []
        warnings: list[str] = []
        chain_id_actual: int | None = None
        rpc_reachable = False

        buyer_resolver, buyer_error = self._build_buyer_resolver()
        if buyer_error:
            errors.append(buyer_error)
        user_signers, signer_error = self._build_signer_registry()
        if signer_error:
            errors.append(signer_error)

        rpc = self._build_rpc_client()
        if rpc is None:
            warnings.append('rpc_url_missing')
        else:
            try:
                chain_id_actual = int(str(rpc.call('eth_chainId', [])), 16)
                rpc_reachable = True
                if chain_id_actual != self._settings.onchain_chain_id:
                    errors.append(
                        f'chain_id_mismatch:expected={self._settings.onchain_chain_id}:actual={chain_id_actual}'
                    )
            except Exception as exc:
                errors.append(f'rpc_unreachable:{exc.__class__.__name__}')

        contracts = self._inspect_contracts(rpc=rpc, rpc_reachable=rpc_reachable, errors=errors)
        signers = self._inspect_signers(errors=errors)
        identity_mappings = self._inspect_identity_mappings(
            buyer_resolver=buyer_resolver,
            user_signers=user_signers,
            errors=errors,
        )

        return OnchainHealthReport(
            healthy=not errors,
            rpc_reachable=rpc_reachable,
            chain_id_expected=self._settings.onchain_chain_id,
            chain_id_actual=chain_id_actual,
            contracts=contracts,
            signers=signers,
            identity_mappings=identity_mappings,
            errors=errors,
            warnings=warnings,
        )

    def _build_rpc_client(self) -> JsonRpcClient | Any | None:
        if self._rpc_client is not None:
            return self._rpc_client
        if not self._settings.onchain_rpc_url.strip():
            return None
        return JsonRpcClient(
            rpc_url=self._settings.onchain_rpc_url,
            timeout_seconds=self._settings.onchain_receipt_timeout_seconds,
        )

    def _build_buyer_resolver(self):
        try:
            return BuyerAddressResolver.from_json(self._settings.buyer_wallet_map_json), None
        except Exception as exc:
            return BuyerAddressResolver(), f'buyer_wallet_map_invalid:{exc}'

    def _build_signer_registry(self):
        try:
            return UserSignerRegistry.from_json(self._settings.user_signer_private_keys_json), None
        except Exception as exc:
            return UserSignerRegistry(), f'user_signer_registry_invalid:{exc}'

    def _inspect_contracts(self, *, rpc, rpc_reachable: bool, errors: list[str]) -> dict[str, dict[str, Any]]:
        contracts = {
            'order_book': self._settings.onchain_order_book_address,
            'payment_router': self._settings.onchain_order_payment_router_address,
            'machine_asset': self._settings.onchain_machine_asset_address,
            'settlement_controller': self._settings.onchain_settlement_controller_address,
            'revenue_vault': self._settings.onchain_revenue_vault_address,
            'pwr': self._settings.onchain_pwr_token_address,
            'usdc': self._settings.onchain_usdc_address,
            'usdt': self._settings.onchain_usdt_address,
        }
        result: dict[str, dict[str, Any]] = {}
        for name, address in contracts.items():
            has_code = None
            if rpc_reachable and rpc is not None:
                try:
                    code = str(rpc.call('eth_getCode', [address, 'latest']))
                    has_code = code not in {'0x', '0x0', ''}
                    if not has_code:
                        errors.append(f'contract_code_missing:{name}:{address.lower()}')
                except Exception as exc:
                    errors.append(f'contract_check_failed:{name}:{exc.__class__.__name__}')
            result[name] = {
                'address': str(address).lower(),
                'has_code': has_code,
            }
        return result

    def _inspect_signers(self, *, errors: list[str]) -> dict[str, dict[str, Any]]:
        signer_keys = {
            'broadcaster': self._settings.onchain_broadcaster_private_key,
            'adapter': self._settings.onchain_adapter_private_key,
            'machine_owner': self._settings.onchain_machine_owner_private_key,
            'buyer': self._settings.onchain_buyer_private_key,
            'platform_treasury': self._settings.onchain_platform_treasury_private_key,
        }
        result: dict[str, dict[str, Any]] = {}
        for role, private_key in signer_keys.items():
            normalized = private_key.strip()
            if not normalized:
                result[role] = {'configured': False, 'wallet_address': None}
                continue
            try:
                wallet = UserSignerRegistry._derive_wallet_address(
                    UserSignerRegistry._normalize_private_key(normalized)
                )
                result[role] = {'configured': True, 'wallet_address': wallet}
            except Exception as exc:
                result[role] = {'configured': True, 'wallet_address': None}
                errors.append(f'signer_invalid:{role}:{exc}')
        return result

    def _inspect_identity_mappings(self, *, buyer_resolver, user_signers, errors: list[str]) -> list[dict[str, Any]]:
        buyer_mapping = getattr(buyer_resolver, '_user_to_wallet', {})
        signer_mapping = getattr(user_signers, '_signers', {})
        rows: list[dict[str, Any]] = []
        for user_id in sorted(set(buyer_mapping) | set(signer_mapping)):
            expected_wallet = buyer_mapping.get(user_id)
            signer = signer_mapping.get(user_id)
            actual_wallet = signer.wallet_address if signer is not None else None
            matches = expected_wallet is not None and actual_wallet is not None and expected_wallet == actual_wallet
            if expected_wallet is not None and actual_wallet is not None and not matches:
                errors.append(
                    f'user_signer_wallet_mismatch:{user_id}:expected={expected_wallet}:actual={actual_wallet}'
                )
            rows.append(
                {
                    'user_id': user_id,
                    'expected_wallet_address': expected_wallet,
                    'signer_wallet_address': actual_wallet,
                    'matches': matches,
                }
            )
        return rows
