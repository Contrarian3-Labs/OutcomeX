import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.onchain.lifecycle_service import reset_onchain_lifecycle_service_cache
from app.core.container import reset_container_cache, get_container
from app.domain.models import Machine, MachineListing, MachineRevenueClaim, Order, RevenueEntry, SettlementRecord
from app.runtime.hardware_simulator import WorkloadSpec
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    previous_onchain_rpc_url = os.environ.get("OUTCOMEX_ONCHAIN_RPC_URL")
    db_path = tmp_path / "machines-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()

    with TestClient(create_app()) as test_client:
        yield test_client

    if previous_onchain_rpc_url is None:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = ""
    else:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = previous_onchain_rpc_url
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()


def _create_machine(client: TestClient, *, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _get_machine(client: TestClient, machine_id: str) -> dict:
    response = client.get("/api/v1/machines")
    assert response.status_code == 200
    for machine in response.json():
        if machine["id"] == machine_id:
            return machine
    raise AssertionError(f"Machine {machine_id} not found in list response")


def test_create_machine_exposes_bootstrap_ownership_state(client: TestClient) -> None:
    machine = _create_machine(client)

    assert machine["owner_user_id"] == "owner-1"
    assert machine["owner_chain_address"] is None
    assert machine["ownership_source"] == "bootstrap"
    assert machine["owner_projection_last_event_id"] is None
    assert machine["owner_projected_at"] is None
    assert machine["pending_transfer_new_owner_user_id"] is None
    assert machine["transfer_ready"] is True
    assert machine["transfer_blocking_reasons"] == []
    assert machine["projected_cents"] == 0
    assert machine["claimed_cents"] == 0
    assert machine["claimable_cents"] == 0
    assert machine["locked_unsettled_revenue_cents"] == 0
    assert machine["locked_unsettled_revenue_pwr"] == 0.0
    assert machine["locked_beneficiary_user_ids"] == []


class StubOnchainLifecycle:
    def __init__(self, *, enabled: bool, onchain_machine_id: str | None = None) -> None:
        self._enabled = enabled
        self._onchain_machine_id = onchain_machine_id
        self.mint_calls: list[dict[str, str]] = []

    def enabled(self) -> bool:
        return self._enabled

    def mint_machine_for_owner(self, *, owner_user_id: str, token_uri: str):
        self.mint_calls.append({"owner_user_id": owner_user_id, "token_uri": token_uri})

        class Receipt:
            def __init__(self, onchain_machine_id: str | None):
                self.onchain_machine_id = onchain_machine_id

        return Receipt(self._onchain_machine_id)


def test_create_machine_mints_onchain_when_runtime_enabled(client: TestClient) -> None:
    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True, onchain_machine_id="101")
    client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub

    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["onchain_machine_id"] == "101"
    assert payload["ownership_source"] == "chain"
    assert stub.mint_calls[0]["owner_user_id"] == "owner-1"


def test_create_machine_backfills_owner_chain_address_when_wallet_mapping_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    db_path = tmp_path / "machines-api-mint.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    monkeypatch.setenv(
        "OUTCOMEX_BUYER_WALLET_MAP_JSON",
        '{"owner-1":"0x1111111111111111111111111111111111111111"}',
    )
    reset_settings_cache()
    reset_container_cache()
    reset_onchain_lifecycle_service_cache()

    stub = StubOnchainLifecycle(enabled=True, onchain_machine_id="101")
    with TestClient(create_app()) as test_client:
        test_client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub
        response = test_client.post(
            "/api/v1/machines",
            json={"display_name": "GANA node", "owner_user_id": "owner-1"},
        )

    payload = response.json()
    assert response.status_code == 201
    assert payload["onchain_machine_id"] == "101"
    assert payload["owner_chain_address"] == "0x1111111111111111111111111111111111111111"
    assert payload["ownership_source"] == "chain"

    reset_settings_cache()
    reset_container_cache()


def test_create_machine_skips_mint_when_onchain_machine_id_is_provided(client: TestClient) -> None:
    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True, onchain_machine_id="999")
    client.app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub

    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1", "onchain_machine_id": "77"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["onchain_machine_id"] == "77"
    assert payload["ownership_source"] == "bootstrap"
    assert stub.mint_calls == []


def test_list_machines_includes_revenue_summary_and_projection_metadata(client: TestClient) -> None:
    machine = _create_machine(client)
    container = get_container()
    with container.session_factory() as db:
        db_machine = db.get(Machine, machine["id"])
        db_machine.owner_chain_address = "0x2222222222222222222222222222222222222222"
        db_machine.owner_projection_last_event_id = "133:11:0xabc:0"
        db_machine.has_unsettled_revenue = True
        db_machine.has_active_tasks = False
        db.add(db_machine)
        db.flush()

        order = Order(
            user_id="buyer-1",
            machine_id=db_machine.id,
            chat_session_id="chat-1",
            user_prompt="deliver",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
        )
        db.add(order)
        db.flush()

        settlement = SettlementRecord(
            order_id=order.id,
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
        )
        db.add(settlement)
        db.flush()

        entry = RevenueEntry(
            order_id=order.id,
            settlement_id=settlement.id,
            machine_id=db_machine.id,
            beneficiary_user_id="owner-1",
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            is_self_use=False,
            is_dividend_eligible=True,
        )
        db.add(entry)
        claim = MachineRevenueClaim(machine_id=db_machine.id, amount_cents=250, tx_hash="0xclaim")
        db.add(claim)
        db.commit()

    payload = _get_machine(client, machine["id"])
    assert payload["owner_chain_address"] == "0x2222222222222222222222222222222222222222"
    assert payload["owner_projection_last_event_id"] == "133:11:0xabc:0"
    assert payload["transfer_ready"] is False
    assert payload["transfer_blocking_reasons"] == ["unsettled_revenue"]
    assert payload["projected_cents"] == 900
    assert payload["claimed_cents"] == 250
    assert payload["claimable_cents"] == 650
    assert payload["projected_pwr"] == 36.0
    assert payload["claimed_pwr"] == 10.0
    assert payload["claimable_pwr"] == 26.0
    assert payload["locked_unsettled_revenue_cents"] == 650
    assert payload["locked_unsettled_revenue_pwr"] == 26.0
    assert payload["locked_beneficiary_user_ids"] == ["owner-1"]
    assert payload["active_listing"] is None


def test_list_machines_includes_active_market_listing_summary(client: TestClient) -> None:
    machine = _create_machine(client)
    container = get_container()
    with container.session_factory() as db:
        db_machine = db.get(Machine, machine["id"])
        db_machine.onchain_machine_id = "7"
        db_machine.owner_chain_address = "0xseller0000000000000000000000000000000000"
        db.add(db_machine)
        db.flush()

        db.add(
            MachineListing(
                onchain_listing_id="11",
                machine_id=db_machine.id,
                onchain_machine_id="7",
                seller_chain_address="0xseller0000000000000000000000000000000000",
                payment_token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                payment_token_symbol="USDC",
                payment_token_decimals=6,
                price_units=1_250_000,
                state="active",
            )
        )
        db.commit()

    payload = _get_machine(client, machine["id"])
    assert payload["active_listing"] is not None
    assert payload["active_listing"]["onchain_listing_id"] == "11"
    assert payload["active_listing"]["seller_chain_address"] == "0xseller0000000000000000000000000000000000"
    assert payload["active_listing"]["payment_token_symbol"] == "USDC"
    assert payload["active_listing"]["payment_token_decimals"] == 6
    assert payload["active_listing"]["price_units"] == 1_250_000
    assert payload["active_listing"]["state"] == "active"


def test_list_machines_exposes_mock_spec_and_runtime_snapshot(client: TestClient) -> None:
    from app.runtime.hardware_simulator import get_shared_hardware_simulator

    machine = _create_machine(client)
    admission = get_shared_hardware_simulator(machine["id"]).submit(
        WorkloadSpec(
            workload_id="machine-view-load",
            capacity_units=6,
            memory_mb=4096,
            duration_ticks=3,
        )
    )
    assert admission.status.value == "running"

    payload = _get_machine(client, machine["id"])

    assert payload["profile_label"] == "Qwen Family"
    assert payload["gpu_spec"] == "Apple Silicon 96GB Unified Memory"
    assert payload["hosted_by"] == "OutcomeX Hosted Rack"
    assert payload["supported_categories"] == [
        "image_generation",
        "video_generation",
        "text_reasoning",
        "multimodal",
        "agentic_workflows",
    ]
    assert payload["runtime_snapshot"]["used_memory_mb"] == 4096
    assert payload["runtime_snapshot"]["total_memory_mb"] == 32768
    assert payload["runtime_snapshot"]["running_count"] == 1
    assert payload["runtime_snapshot"]["queued_count"] == 0
    assert payload["runtime_snapshot"]["memory_utilization"] == 0.125
    assert payload["runtime_snapshot"]["capacity_utilization"] == 0.25
    assert payload["availability"] == 75
    assert payload["confirmed_revenue_30d_pwr"] == 0.0
    assert payload["claimable_pwr"] == 0.0
    assert payload["indicative_apr"] == 0.0


def test_machine_runtime_snapshot_is_isolated_per_machine(client: TestClient) -> None:
    from app.runtime.hardware_simulator import get_shared_hardware_simulator

    machine_a = _create_machine(client, owner_user_id="owner-a")
    machine_b = _create_machine(client, owner_user_id="owner-b")

    simulator_a = get_shared_hardware_simulator(machine_a["id"])
    admission = simulator_a.submit(
        WorkloadSpec(
            workload_id="machine-a-load",
            capacity_units=6,
            memory_mb=4096,
            duration_ticks=3,
        )
    )
    assert admission.status.value == "running"

    payload_a = _get_machine(client, machine_a["id"])
    payload_b = _get_machine(client, machine_b["id"])

    assert payload_a["runtime_snapshot"]["used_memory_mb"] == 4096
    assert payload_a["availability"] == 75
    assert payload_b["runtime_snapshot"]["used_memory_mb"] == 0
    assert payload_b["runtime_snapshot"]["running_count"] == 0
    assert payload_b["availability"] == 100
