import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import get_container, reset_container_cache
from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import Machine, Order, Payment
from app.main import create_app
from app.onchain.claim_state_reader import get_settlement_claim_state_reader
from app.onchain.tx_sender import CLAIM_MACHINE_REVENUE_SELECTOR, CLAIM_PLATFORM_REVENUE_SELECTOR, CLAIM_REFUND_SELECTOR


class StubBroadcast:
    def __init__(self, *, tx_hash: str) -> None:
        self.tx_hash = tx_hash
        self.receipt = None


class StubOnchainLifecycle:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self.user_calls: list[dict[str, str]] = []
        self.treasury_calls: list[dict[str, str]] = []

    def enabled(self) -> bool:
        return self._enabled

    def send_as_user(self, *, user_id: str, write_result):
        self.user_calls.append(
            {
                "user_id": user_id,
                "method_name": write_result.method_name,
                "contract_name": write_result.contract_name,
            }
        )
        return StubBroadcast(tx_hash=f"0x{write_result.method_name.lower()}")

    def send_as_treasury(self, *, write_result):
        self.treasury_calls.append(
            {
                "method_name": write_result.method_name,
                "contract_name": write_result.contract_name,
            }
        )
        return StubBroadcast(tx_hash=f"0x{write_result.method_name.lower()}")


class StubSettlementClaimStateReader:
    def __init__(self, *, refund_amount: int = 500, platform_amount: int = 100) -> None:
        self.refund_amount = refund_amount
        self.platform_amount = platform_amount
        self.refund_queries: list[dict[str, str]] = []
        self.platform_queries: list[str] = []

    def refundable_amount(self, *, user_id: str, currency: str) -> int:
        self.refund_queries.append({"user_id": user_id, "currency": currency})
        return self.refund_amount

    def platform_accrued_amount(self, *, currency: str) -> int:
        self.platform_queries.append(currency)
        return self.platform_amount


@pytest.fixture
def client(tmp_path) -> tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader]:
    previous_database_url = os.environ.get("OUTCOMEX_DATABASE_URL")
    previous_auto_create_tables = os.environ.get("OUTCOMEX_AUTO_CREATE_TABLES")
    previous_onchain_rpc_url = os.environ.get("OUTCOMEX_ONCHAIN_RPC_URL")
    previous_buyer_wallet_map_json = os.environ.get("OUTCOMEX_BUYER_WALLET_MAP_JSON")
    db_path = tmp_path / "claims-api.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = "http://127.0.0.1:8545"
    os.environ['OUTCOMEX_BUYER_WALLET_MAP_JSON'] = '{"buyer-1":"0x1111111111111111111111111111111111111111"}'
    reset_settings_cache()
    reset_container_cache()

    from app.onchain.lifecycle_service import get_onchain_lifecycle_service

    stub = StubOnchainLifecycle(enabled=True)
    claim_reader = StubSettlementClaimStateReader()
    app = create_app()
    app.dependency_overrides[get_onchain_lifecycle_service] = lambda: stub
    app.dependency_overrides[get_settlement_claim_state_reader] = lambda: claim_reader
    with TestClient(app) as test_client:
        yield test_client, stub, claim_reader

    if previous_database_url is None:
        os.environ.pop("OUTCOMEX_DATABASE_URL", None)
    else:
        os.environ["OUTCOMEX_DATABASE_URL"] = previous_database_url
    if previous_auto_create_tables is None:
        os.environ.pop("OUTCOMEX_AUTO_CREATE_TABLES", None)
    else:
        os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = previous_auto_create_tables
    if previous_onchain_rpc_url is None:
        os.environ.pop("OUTCOMEX_ONCHAIN_RPC_URL", None)
    else:
        os.environ["OUTCOMEX_ONCHAIN_RPC_URL"] = previous_onchain_rpc_url
    if previous_buyer_wallet_map_json is None:
        os.environ.pop("OUTCOMEX_BUYER_WALLET_MAP_JSON", None)
    else:
        os.environ["OUTCOMEX_BUYER_WALLET_MAP_JSON"] = previous_buyer_wallet_map_json
    reset_settings_cache()
    reset_container_cache()


def _seed_machine(
    *,
    owner_user_id: str = "owner-1",
    onchain_machine_id: str = "7",
    has_unsettled_revenue: bool = True,
) -> Machine:
    with get_container().session_factory() as db:
        machine = Machine(
            display_name="Claim Node",
            owner_user_id=owner_user_id,
            onchain_machine_id=onchain_machine_id,
            ownership_source="chain",
            has_unsettled_revenue=has_unsettled_revenue,
        )
        db.add(machine)
        db.commit()
        db.refresh(machine)
        return machine


def _seed_refundable_order(*, machine_id: str, user_id: str = "buyer-1", currency: str = "USDC") -> Order:
    with get_container().session_factory() as db:
        order = Order(
            user_id=user_id,
            machine_id=machine_id,
            chat_session_id="chat-claim",
            user_prompt="Need refund",
            recommended_plan_summary="Refund path",
            quoted_amount_cents=500,
            state=OrderState.CANCELLED,
            settlement_state=SettlementState.DISTRIBUTED,
        )
        db.add(order)
        db.flush()
        payment = Payment(
            order_id=order.id,
            provider="hsp",
            amount_cents=500,
            currency=currency,
            state=PaymentState.SUCCEEDED,
        )
        db.add(payment)
        db.commit()
        db.refresh(order)
        return order


def test_claim_machine_revenue_broadcasts_real_onchain_method(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=True)

    response = test_client.post(f"/api/v1/revenue/machines/{machine.id}/claim")

    assert response.status_code == 200
    assert response.json() == {
        "machine_id": machine.id,
        "onchain_machine_id": "7",
        "claimant_user_id": "owner-1",
        "tx_hash": "0xclaimmachinerevenue",
        "contract_name": "RevenueVault",
        "method_name": "claimMachineRevenue",
    }
    assert stub.user_calls == [
        {
            "user_id": "owner-1",
            "method_name": "claimMachineRevenue",
            "contract_name": "RevenueVault",
        }
    ]


def test_claim_machine_revenue_rejects_when_no_unsettled_balance(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=False)

    response = test_client.post(f"/api/v1/revenue/machines/{machine.id}/claim")

    assert response.status_code == 409
    assert response.json()["detail"] == "Machine has no unsettled revenue to claim"
    assert stub.user_calls == []


def test_claim_refund_uses_buyer_signer_and_payment_currency(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=False)
    order = _seed_refundable_order(machine_id=machine.id, user_id="buyer-1", currency="USDC")

    response = test_client.post(f"/api/v1/settlement/orders/{order.id}/claim-refund")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order.id,
        "claimant_user_id": "buyer-1",
        "currency": "USDC",
        "tx_hash": "0xclaimrefund",
        "contract_name": "SettlementController",
        "method_name": "claimRefund",
    }
    assert stub.user_calls == [
        {
            "user_id": "buyer-1",
            "method_name": "claimRefund",
            "contract_name": "SettlementController",
        }
    ]


def test_claim_platform_revenue_uses_treasury_signer(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client

    response = test_client.post("/api/v1/settlement/platform/claim", json={"currency": "USDC"})

    assert response.status_code == 200
    assert response.json() == {
        "currency": "USDC",
        "tx_hash": "0xclaimplatformrevenue",
        "contract_name": "SettlementController",
        "method_name": "claimPlatformRevenue",
    }
    assert stub.treasury_calls == [
        {
            "method_name": "claimPlatformRevenue",
            "contract_name": "SettlementController",
        }
    ]



def test_claim_machine_revenue_user_sign_returns_call_intent_without_broadcast(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=True)
    stub.user_calls.clear()

    response = test_client.post(f"/api/v1/revenue/machines/{machine.id}/claim?mode=user_sign")

    assert response.status_code == 200
    payload = response.json()
    assert payload["machine_id"] == machine.id
    assert payload["onchain_machine_id"] == "7"
    assert payload["claimant_user_id"] == "owner-1"
    assert payload["mode"] == "user_sign"
    assert payload["contract_name"] == "RevenueVault"
    assert payload["method_name"] == "claimMachineRevenue"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000136"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"machine_id": "7"}
    assert payload["calldata"].startswith(CLAIM_MACHINE_REVENUE_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.user_calls == []


def test_claim_refund_user_sign_returns_call_intent_without_broadcast(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=False)
    order = _seed_refundable_order(machine_id=machine.id, user_id="buyer-1", currency="USDC")
    stub.user_calls.clear()

    response = test_client.post(f"/api/v1/settlement/orders/{order.id}/claim-refund?mode=user_sign")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == order.id
    assert payload["claimant_user_id"] == "buyer-1"
    assert payload["currency"] == "USDC"
    assert payload["mode"] == "user_sign"
    assert payload["contract_name"] == "SettlementController"
    assert payload["method_name"] == "claimRefund"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000135"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"payment_token_address": "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"}
    assert payload["calldata"].startswith(CLAIM_REFUND_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.user_calls == []


def test_claim_platform_revenue_user_sign_returns_call_intent_without_broadcast(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, _claim_reader = client
    stub.treasury_calls.clear()

    response = test_client.post("/api/v1/settlement/platform/claim?mode=user_sign", json={"currency": "USDC"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["currency"] == "USDC"
    assert payload["mode"] == "user_sign"
    assert payload["contract_name"] == "SettlementController"
    assert payload["method_name"] == "claimPlatformRevenue"
    assert payload["contract_address"] == "0x0000000000000000000000000000000000000135"
    assert payload["chain_id"] == 133
    assert payload["submit_payload"] == {"payment_token_address": "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"}
    assert payload["calldata"].startswith(CLAIM_PLATFORM_REVENUE_SELECTOR)
    assert "tx_hash" not in payload
    assert stub.treasury_calls == []


def test_claim_refund_rejects_when_no_onchain_refund_balance(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, claim_reader = client
    machine = _seed_machine(owner_user_id="owner-1", has_unsettled_revenue=False)
    order = _seed_refundable_order(machine_id=machine.id, user_id="buyer-1", currency="USDC")
    claim_reader.refund_amount = 0

    response = test_client.post(f"/api/v1/settlement/orders/{order.id}/claim-refund")

    assert response.status_code == 409
    assert response.json()["detail"] == "Refund has no claimable onchain balance"
    assert stub.user_calls == []


def test_claim_platform_revenue_rejects_when_no_onchain_platform_balance(
    client: tuple[TestClient, StubOnchainLifecycle, StubSettlementClaimStateReader],
) -> None:
    test_client, stub, claim_reader = client
    claim_reader.platform_amount = 0

    response = test_client.post("/api/v1/settlement/platform/claim", json={"currency": "USDC"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Platform has no claimable onchain revenue"
    assert stub.treasury_calls == []
