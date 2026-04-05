import json
import subprocess
from datetime import datetime, timezone

import pytest

from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriteResult
from app.onchain.tx_sender import CastTransactionSender


def _write_result() -> OrderWriteResult:
    return OrderWriteResult(
        tx_hash="0xsynthetic",
        submitted_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
        chain_id=133,
        contract_name="OrderPaymentRouter",
        contract_address="0x0000000000000000000000000000000000000134",
        method_name="createPaidOrderByAdapter",
        idempotency_key="key",
        payload={
            "buyer": "0x2222222222222222222222222222222222222222",
            "machine_id": "7",
            "amount": 500,
            "payment_token_address": "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        },
    )


def test_cast_sender_replaces_tx_hash_from_cast_output() -> None:
    captured = {}

    def fake_runner(command, capture_output, text, check):
        captured["command"] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps({"transactionHash": "0xLiveHash"}),
            stderr="",
        )

    sender = CastTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        contracts_registry=ContractsRegistry(),
        runner=fake_runner,
    )

    result = sender.send(_write_result())

    assert captured["command"][:4] == [
        "cast",
        "send",
        "0x0000000000000000000000000000000000000134",
        "createPaidOrderByAdapter(address,uint256,uint256,address)",
    ]
    assert result.tx_hash == "0xlivehash"


def test_cast_sender_leaves_non_hsp_methods_unchanged() -> None:
    sender = CastTransactionSender(
        rpc_url="https://rpc.local",
        private_key="0xabc",
        contracts_registry=ContractsRegistry(),
        runner=lambda *args, **kwargs: pytest.fail("runner should not be called"),
    )
    write_result = _write_result()
    write_result = OrderWriteResult(
        tx_hash=write_result.tx_hash,
        submitted_at=write_result.submitted_at,
        chain_id=write_result.chain_id,
        contract_name=write_result.contract_name,
        contract_address=write_result.contract_address,
        method_name="createOrderAndPayWithUSDC",
        idempotency_key=write_result.idempotency_key,
        payload=write_result.payload,
    )

    result = sender.send(write_result)

    assert result == write_result
