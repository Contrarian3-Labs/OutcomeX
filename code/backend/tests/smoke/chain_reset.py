import os
import shutil
from pathlib import Path

import requests
from web3 import Web3


def ensure_reusable_snapshot(*, snapshot_file: Path, rpc_call):
    existing_snapshot = snapshot_file.read_text(encoding="utf-8").strip() if snapshot_file.exists() else ""
    if existing_snapshot:
        try:
            reverted = bool(rpc_call("evm_revert", [existing_snapshot]))
        except Exception:
            reverted = False
        if reverted:
            new_snapshot = str(rpc_call("evm_snapshot", []))
            snapshot_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot_file.write_text(new_snapshot, encoding="utf-8")
            return new_snapshot

    new_snapshot = str(rpc_call("evm_snapshot", []))
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_text(new_snapshot, encoding="utf-8")
    return new_snapshot


def prepare_clean_run_state(*, snapshot_file: Path, rpc_call, cleanup_paths: tuple[Path, ...]):
    snapshot_id = ensure_reusable_snapshot(snapshot_file=snapshot_file, rpc_call=rpc_call)
    for path in cleanup_paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    return snapshot_id


def resolve_smoke_onchain_addresses() -> dict[str, str]:
    return {
        "pwr": Web3.to_checksum_address(os.getenv("OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS", "0x9E545E3C0baAB3E08CdfD552C960A1050f373042")),
        "router": Web3.to_checksum_address(os.getenv("OUTCOMEX_ONCHAIN_ORDER_PAYMENT_ROUTER_ADDRESS", "0x95401dc811bb5740090279Ba06cfA8fcF6113778")),
        "orderbook": Web3.to_checksum_address(os.getenv("OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS", "0xf5059a5D33d5853360D16C683c16e67980206f36")),
        "settlement": Web3.to_checksum_address(os.getenv("OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS", "0x851356ae760d987E095750cCeb3bC6014560891C")),
        "revenue": Web3.to_checksum_address(os.getenv("OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS", "0x1613beB3B2C4f22Ee086B2b38C1476A3cE7f78E8")),
    }


def smoke_preflight_cleanup_paths(*, report_path: Path) -> tuple[Path, ...]:
    return (report_path,)


def default_cleanup_paths(*, report_path: Path) -> tuple[Path, ...]:
    paths: list[Path] = [report_path]
    database_url = os.getenv("OUTCOMEX_DATABASE_URL", "").strip()
    if database_url.startswith("sqlite+pysqlite:///"):
        sqlite_path = database_url.removeprefix("sqlite+pysqlite:///")
        if sqlite_path:
            paths.append(Path(sqlite_path))
    output_root = os.getenv("OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT", "").strip()
    if output_root:
        paths.append(Path(output_root))
    return tuple(paths)


def json_rpc_call(*, rpc_url: str, method: str, params: list[object]):
    response = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error") is not None:
        raise RuntimeError(f"json_rpc_error:{payload['error']}")
    return payload.get("result")
