from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.onchain.order_writer import OrderWriteResult

CREATE_ORDER_BY_ADAPTER_SELECTOR = "0xc859da5c"
PAY_ORDER_BY_ADAPTER_SELECTOR = "0xec08a5d2"
CREATE_ORDER_AND_PAY_WITH_USDC_SELECTOR = "0xc73f27f1"
CREATE_ORDER_AND_PAY_WITH_USDT_SELECTOR = "0x3d961057"
CREATE_ORDER_AND_PAY_WITH_PWR_SELECTOR = "0x321a55a2"
PAY_WITH_PWR_SELECTOR = "0xd4099cc2"
MARK_PREVIEW_READY_SELECTOR = "0x9bd0cb73"
CONFIRM_RESULT_SELECTOR = "0xeb05cf51"
REJECT_VALID_PREVIEW_SELECTOR = "0xd5518ff7"
REFUND_FAILED_OR_NO_VALID_PREVIEW_SELECTOR = "0x8d38c3df"
MINT_MACHINE_SELECTOR = "0xcafa2ed4"
CLAIM_MACHINE_REVENUE_SELECTOR = "0x379607f5"
CLAIM_REFUND_SELECTOR = "0xbffa55d5"
CLAIM_PLATFORM_REVENUE_SELECTOR = "0x23037e0c"

TRANSACTION_METHODS = {
    "createOrderByAdapter",
    "payOrderByAdapter",
    "markPreviewReady",
    "confirmResult",
    "rejectValidPreview",
    "refundFailedOrNoValidPreview",
    "mintMachine",
    "claimMachineRevenue",
    "claimRefund",
    "claimPlatformRevenue",
}

ENCODABLE_METHODS = TRANSACTION_METHODS | {
    "createOrderAndPayWithUSDC",
    "createOrderAndPayWithUSDT",
    "createOrderAndPayWithPWR",
    "payWithPWR",
}


class TransactionSender(Protocol):
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        ...


class NullTransactionSender:
    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        return write_result


def encode_contract_call(write_result: OrderWriteResult) -> str | None:
    if write_result.method_name not in ENCODABLE_METHODS:
        return None
    return PythonTransactionSender._encode_method_call(
        method_name=write_result.method_name,
        payload=write_result.payload,
    )


class JsonRpcClient:
    def __init__(self, *, rpc_url: str, timeout_seconds: float = 10.0) -> None:
        self._rpc_url = rpc_url
        self._timeout_seconds = timeout_seconds

    def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(self._rpc_url, json=payload)
            response.raise_for_status()
            body = response.json()
        if body.get("error") is not None:
            raise RuntimeError(f"json_rpc_error:{body['error']}")
        return body.get("result")


class PythonTransactionSender:
    """Pure-Python JSON-RPC sender for selected OutcomeX contract methods."""

    def __init__(
        self,
        *,
        rpc_url: str,
        private_key: str | None = None,
        method_private_keys: dict[str, str] | None = None,
        rpc_timeout_seconds: float = 10.0,
        rpc_client: JsonRpcClient | Any | None = None,
        account_factory=None,
        contracts_registry: Any | None = None,
    ) -> None:
        self._default_private_key = (private_key or "").strip() or None
        self._method_private_keys = {
            method_name: key.strip()
            for method_name, key in (method_private_keys or {}).items()
            if key.strip()
        }
        self._rpc_client = rpc_client or JsonRpcClient(
            rpc_url=rpc_url,
            timeout_seconds=rpc_timeout_seconds,
        )
        self._account_factory = account_factory
        self._contracts_registry = contracts_registry

    def send(self, write_result: OrderWriteResult) -> OrderWriteResult:
        method_name = write_result.method_name
        if method_name not in TRANSACTION_METHODS:
            return write_result

        private_key = self._resolve_private_key(method_name)
        if private_key is None:
            return write_result

        data = self._encode_method_call(method_name=method_name, payload=write_result.payload)
        account = self._build_account(private_key)
        sender_address = str(account.address)
        sender_address_normalized = sender_address.lower()
        chain_id = int(self._rpc_client.call("eth_chainId", []), 16)
        if write_result.chain_id and write_result.chain_id != chain_id:
            raise RuntimeError(
                f"chain_id_mismatch:write={write_result.chain_id},rpc={chain_id}"
            )

        nonce = int(self._rpc_client.call("eth_getTransactionCount", [sender_address_normalized, "pending"]), 16)
        gas_price = int(self._rpc_client.call("eth_gasPrice", []), 16)
        tx = {
            "to": self._checksum_address(str(write_result.contract_address)),
            "from": self._checksum_address(sender_address),
            "data": data,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": gas_price,
            "value": 0,
        }
        tx["gas"] = int(self._rpc_client.call("eth_estimateGas", [tx]), 16)

        signed = account.sign_transaction(tx)
        raw_tx = self._raw_transaction_hex(signed)
        tx_hash = str(self._rpc_client.call("eth_sendRawTransaction", [raw_tx])).lower()
        return replace(write_result, tx_hash=tx_hash)

    def _resolve_private_key(self, method_name: str) -> str | None:
        method_key = self._method_private_keys.get(method_name)
        if method_key:
            return method_key
        return self._default_private_key

    def _build_account(self, private_key: str):
        if self._account_factory is not None:
            return self._account_factory(private_key)
        try:
            from eth_account import Account
        except ModuleNotFoundError as exc:
            raise RuntimeError("eth-account is required for PythonTransactionSender") from exc
        return Account.from_key(private_key)

    @staticmethod
    def _checksum_address(value: str) -> str:
        try:
            from eth_utils import to_checksum_address
        except ModuleNotFoundError as exc:
            raise RuntimeError("eth-utils is required for PythonTransactionSender") from exc
        return to_checksum_address(value)

    @staticmethod
    def _raw_transaction_hex(signed: Any) -> str:
        raw_tx = getattr(signed, "raw_transaction", None)
        if raw_tx is None:
            raw_tx = getattr(signed, "rawTransaction", None)
        if raw_tx is None:
            raise RuntimeError("signed_transaction_missing_raw_bytes")
        return raw_tx.hex() if hasattr(raw_tx, "hex") else str(raw_tx)

    @classmethod
    def _encode_method_call(cls, *, method_name: str, payload: dict[str, Any]) -> str:
        if method_name == "createOrderAndPayWithUSDC":
            valid_after = payload.get("valid_after", 0)
            valid_before = payload.get("valid_before", 0)
            nonce = payload.get("nonce", "0x0")
            v = payload.get("v", 0)
            r = payload.get("r", "0x0")
            s = payload.get("s", "0x0")
            encoded = [
                cls._encode_uint256(payload["machine_id"]),
                cls._encode_uint256(payload["gross_amount_cents"]),
                cls._encode_uint256(valid_after),
                cls._encode_uint256(valid_before),
                cls._encode_bytes32(nonce),
                cls._encode_uint256(v),
                cls._encode_bytes32(r),
                cls._encode_bytes32(s),
            ]
            return CREATE_ORDER_AND_PAY_WITH_USDC_SELECTOR + "".join(encoded)
        if method_name == "createOrderAndPayWithUSDT":
            permit_nonce = payload.get("permit_nonce", payload.get("nonce", 0))
            deadline = payload.get("deadline", 0)
            signature = payload.get("signature", "0x")
            encoded = [
                cls._encode_uint256(payload["machine_id"]),
                cls._encode_uint256(payload["gross_amount_cents"]),
                cls._encode_uint256(permit_nonce),
                cls._encode_uint256(deadline),
                cls._encode_uint256(160),
                cls._encode_bytes(signature),
            ]
            return CREATE_ORDER_AND_PAY_WITH_USDT_SELECTOR + "".join(encoded)
        if method_name == "createOrderAndPayWithPWR":
            pwr_amount = payload.get("pwr_amount", payload["gross_amount_cents"])
            encoded = [
                cls._encode_uint256(payload["machine_id"]),
                cls._encode_uint256(pwr_amount),
            ]
            return CREATE_ORDER_AND_PAY_WITH_PWR_SELECTOR + "".join(encoded)
        if method_name == "createOrderByAdapter":
            encoded = [
                cls._encode_address(payload["buyer"]),
                cls._encode_uint256(payload["machine_id"]),
                cls._encode_uint256(payload["gross_amount"]),
            ]
            return CREATE_ORDER_BY_ADAPTER_SELECTOR + "".join(encoded)
        if method_name == "payOrderByAdapter":
            encoded = [
                cls._encode_uint256(payload["order_id"]),
                cls._encode_uint256(payload["amount"]),
                cls._encode_address(payload["payment_token_address"]),
            ]
            return PAY_ORDER_BY_ADAPTER_SELECTOR + "".join(encoded)
        if method_name == "mintMachine":
            return (
                MINT_MACHINE_SELECTOR
                + cls._encode_address(payload["to"])
                + cls._encode_uint256(64)
                + cls._encode_string(payload["uri"])
            )
        if method_name == "claimMachineRevenue":
            return CLAIM_MACHINE_REVENUE_SELECTOR + cls._encode_uint256(payload["machine_id"])
        if method_name == "claimRefund":
            return CLAIM_REFUND_SELECTOR + cls._encode_address(payload["payment_token_address"])
        if method_name == "claimPlatformRevenue":
            return CLAIM_PLATFORM_REVENUE_SELECTOR + cls._encode_address(payload["payment_token_address"])
        if method_name == "payWithPWR":
            return PAY_WITH_PWR_SELECTOR + cls._encode_uint256(payload["order_id"]) + cls._encode_uint256(
                payload.get("pwr_amount", payload["gross_amount_cents"])
            )

        order_id = cls._encode_uint256(payload["order_id"])
        if method_name == "confirmResult":
            return CONFIRM_RESULT_SELECTOR + order_id
        if method_name == "rejectValidPreview":
            return REJECT_VALID_PREVIEW_SELECTOR + order_id
        if method_name == "refundFailedOrNoValidPreview":
            return REFUND_FAILED_OR_NO_VALID_PREVIEW_SELECTOR + order_id
        if method_name == "markPreviewReady":
            valid_preview = payload.get("valid_preview")
            if valid_preview is None:
                valid_preview = (
                    str(payload.get("preview_state", "")).lower() == "ready"
                    and str(payload.get("execution_state", "")).lower() == "succeeded"
                )
            return MARK_PREVIEW_READY_SELECTOR + order_id + cls._encode_bool(valid_preview)
        raise RuntimeError(f"unsupported_method_name:{method_name}")

    @staticmethod
    def _encode_uint256(value: int | str) -> str:
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped.startswith("0x"):
                parsed = int(stripped, 16)
            else:
                try:
                    parsed = int(stripped, 10)
                except ValueError:
                    if stripped.startswith("-"):
                        parsed = int(stripped, 10)
                    else:
                        compact = stripped.replace("-", "")
                        parsed = int(compact, 16)
        else:
            parsed = int(value)
        if parsed < 0:
            raise ValueError("uint256_must_be_non_negative")
        return hex(parsed)[2:].rjust(64, "0")

    @staticmethod
    def _encode_bytes32(value: str | int) -> str:
        if isinstance(value, int):
            return PythonTransactionSender._encode_uint256(value)

        normalized = str(value).strip().lower()
        if normalized.startswith("0x"):
            normalized = normalized[2:]
        if not normalized:
            normalized = "0"
        return normalized.rjust(64, "0")[-64:]

    @staticmethod
    def _encode_address(value: str) -> str:
        normalized = str(value).strip().lower().removeprefix("0x")
        return normalized.rjust(64, "0")

    @staticmethod
    def _encode_string(value: str) -> str:
        encoded = str(value).encode("utf-8").hex()
        padded = encoded.ljust(((len(encoded) + 63) // 64) * 64, "0")
        return PythonTransactionSender._encode_uint256(len(str(value).encode("utf-8"))) + padded

    @staticmethod
    def _encode_bytes(value: str | bytes) -> str:
        if isinstance(value, bytes):
            encoded = value.hex()
        else:
            normalized = str(value).strip()
            if normalized.startswith("0x"):
                encoded = normalized[2:]
            else:
                encoded = normalized.encode("utf-8").hex()
        padded = encoded.ljust(((len(encoded) + 63) // 64) * 64, "0")
        return PythonTransactionSender._encode_uint256(len(encoded) // 2) + padded

    @staticmethod
    def _encode_bool(value: bool | str | int) -> str:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1"}:
                parsed = True
            elif normalized in {"false", "0"}:
                parsed = False
            else:
                raise ValueError(f"invalid_bool:{value}")
        else:
            parsed = bool(value)
        return ("1" if parsed else "0").rjust(64, "0")


def get_onchain_transaction_sender() -> TransactionSender:
    settings = get_settings()
    if not settings.onchain_rpc_url:
        return NullTransactionSender()

    method_private_keys = {
        "createOrderByAdapter": settings.onchain_adapter_private_key or settings.onchain_broadcaster_private_key,
        "payOrderByAdapter": settings.onchain_adapter_private_key or settings.onchain_broadcaster_private_key,
        "markPreviewReady": settings.onchain_machine_owner_private_key,
        "confirmResult": settings.onchain_buyer_private_key,
        "rejectValidPreview": settings.onchain_buyer_private_key,
        "refundFailedOrNoValidPreview": settings.onchain_buyer_private_key,
        "mintMachine": settings.onchain_broadcaster_private_key,
        "claimMachineRevenue": settings.onchain_machine_owner_private_key,
        "claimRefund": settings.onchain_buyer_private_key,
        "claimPlatformRevenue": settings.onchain_platform_treasury_private_key,
    }
    default_private_key = settings.onchain_adapter_private_key or settings.onchain_broadcaster_private_key
    if not default_private_key and not any(method_private_keys.values()):
        return NullTransactionSender()

    return PythonTransactionSender(
        rpc_url=settings.onchain_rpc_url,
        private_key=default_private_key,
        method_private_keys=method_private_keys,
        rpc_timeout_seconds=settings.onchain_tx_timeout_seconds,
    )
