from pathlib import Path

from web3 import Web3

from tests.smoke.chain_reset import (
    default_cleanup_paths,
    ensure_reusable_snapshot,
    prepare_clean_run_state,
    resolve_smoke_onchain_addresses,
    smoke_preflight_cleanup_paths,
)


def test_revert_existing_snapshot_and_rotate_new_baseline(tmp_path):
    calls = []
    responses = {
        ("evm_revert", ("0xold",)): True,
        ("evm_snapshot", ()): "0xnew",
    }

    def rpc(method, params):
        calls.append((method, tuple(params)))
        return responses[(method, tuple(params))]

    snapshot_file = tmp_path / "snapshot.txt"
    snapshot_file.write_text("0xold", encoding="utf-8")

    snapshot_id = ensure_reusable_snapshot(snapshot_file=snapshot_file, rpc_call=rpc)

    assert snapshot_id == "0xnew"
    assert snapshot_file.read_text(encoding="utf-8") == "0xnew"
    assert calls == [("evm_revert", ("0xold",)), ("evm_snapshot", ())]


def test_failed_revert_creates_new_baseline_and_cleans_paths(tmp_path):
    calls = []

    def rpc(method, params):
        calls.append((method, tuple(params)))
        if method == "evm_revert":
            return False
        if method == "evm_snapshot":
            return "0xfresh"
        raise AssertionError(method)

    snapshot_file = tmp_path / "snapshot.txt"
    snapshot_file.write_text("0xstale", encoding="utf-8")
    stale_file = tmp_path / "report.json"
    stale_file.write_text("x", encoding="utf-8")
    stale_dir = tmp_path / "artifacts"
    stale_dir.mkdir()
    (stale_dir / "out.txt").write_text("y", encoding="utf-8")

    snapshot_id = prepare_clean_run_state(
        snapshot_file=snapshot_file,
        rpc_call=rpc,
        cleanup_paths=(stale_file, stale_dir),
    )

    assert snapshot_id == "0xfresh"
    assert not stale_file.exists()
    assert not stale_dir.exists()
    assert calls == [("evm_revert", ("0xstale",)), ("evm_snapshot", ())]


def test_default_cleanup_paths_include_report_db_and_output(monkeypatch, tmp_path):
    db_path = tmp_path / "smoke.db"
    output_path = tmp_path / "outputs"
    report_path = tmp_path / "report.json"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AGENTSKILLOS_EXECUTION_OUTPUT_ROOT", str(output_path))

    paths = default_cleanup_paths(report_path=report_path)

    assert report_path in paths
    assert db_path in paths
    assert output_path in paths


def test_resolve_smoke_onchain_addresses_prefers_env(monkeypatch):
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_PWR_TOKEN_ADDRESS", "0x00000000000000000000000000000000000000a1")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_PAYMENT_ROUTER_ADDRESS", "0x00000000000000000000000000000000000000a2")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_ORDER_BOOK_ADDRESS", "0x00000000000000000000000000000000000000a3")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_SETTLEMENT_CONTROLLER_ADDRESS", "0x00000000000000000000000000000000000000a4")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_REVENUE_VAULT_ADDRESS", "0x00000000000000000000000000000000000000a5")

    addresses = resolve_smoke_onchain_addresses()

    assert addresses == {
        "pwr": Web3.to_checksum_address("0x00000000000000000000000000000000000000a1"),
        "router": Web3.to_checksum_address("0x00000000000000000000000000000000000000a2"),
        "orderbook": Web3.to_checksum_address("0x00000000000000000000000000000000000000a3"),
        "settlement": Web3.to_checksum_address("0x00000000000000000000000000000000000000a4"),
        "revenue": Web3.to_checksum_address("0x00000000000000000000000000000000000000a5"),
    }


def test_smoke_preflight_cleanup_paths_only_keeps_report(tmp_path):
    report_path = tmp_path / "report.json"

    paths = smoke_preflight_cleanup_paths(report_path=report_path)

    assert paths == (report_path,)
