from __future__ import annotations

import httpx

from app.core.config import Settings
from app.onchain.lifecycle_service import OnchainLifecycleService


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_fetch_machine_minted_logs_batches_by_max_block_span(monkeypatch) -> None:
    requests: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, json: dict):
            requests.append(json)
            method = json["method"]
            if method == "eth_blockNumber":
                return _FakeResponse({"result": hex(15)})
            if method == "eth_getLogs":
                params = json["params"][0]
                return _FakeResponse(
                    {
                        "result": [
                            {
                                "blockNumber": params["fromBlock"],
                                "transactionHash": f"0x{params['fromBlock'][2:]:0>64}",
                            }
                        ]
                    }
                )
            raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(httpx, "Client", FakeClient)

    service = OnchainLifecycleService(
        settings=Settings(
            onchain_rpc_url="https://rpc.example",
            onchain_machine_asset_address="0x0000000000000000000000000000000000000132",
            onchain_indexer_bootstrap_block=5,
            onchain_indexer_max_block_span=2,
        )
    )

    logs = service._fetch_machine_minted_logs(from_block=12)

    assert [request["method"] for request in requests] == ["eth_blockNumber", "eth_getLogs", "eth_getLogs"]
    assert requests[1]["params"][0]["fromBlock"] == hex(12)
    assert requests[1]["params"][0]["toBlock"] == hex(13)
    assert requests[2]["params"][0]["fromBlock"] == hex(14)
    assert requests[2]["params"][0]["toBlock"] == hex(15)
    assert len(logs) == 2
