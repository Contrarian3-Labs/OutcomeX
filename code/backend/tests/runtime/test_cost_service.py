import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app
from app.runtime.cost_service import RuntimeCostService


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "runtime-cost.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_ENV", "dev")
    reset_settings_cache()
    reset_container_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient, owner_user_id: str = "owner-1") -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": owner_user_id},
    )
    assert response.status_code == 201
    return response.json()


def _create_order(client: TestClient, machine_id: str, quoted_amount_cents: int = 1000) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine_id,
            "chat_session_id": "chat-1",
            "user_prompt": "Build a launch workflow for a robotics demo",
            "quoted_amount_cents": quoted_amount_cents,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_quote_for_order_amount_has_deterministic_split_math() -> None:
    service = RuntimeCostService()

    quote = service.quote_for_order_amount(1000)

    assert quote.runtime_cost_cents == 750
    assert quote.official_quote_cents == 1000
    assert quote.platform_fee_cents == 100
    assert quote.machine_share_cents == 900
    assert quote.pwr_quote == "36.0000"


def test_quote_for_order_amount_exposes_pwr_anchor_metadata() -> None:
    service = RuntimeCostService()

    quote = service.quote_for_order_amount(1000)

    assert quote.pwr_quote == "36.0000"
    assert quote.pwr_anchor_price_cents == 25
    assert quote.pricing_version == "phase1_v3"


def test_quote_for_prompt_is_deterministic() -> None:
    service = RuntimeCostService()
    prompt = "Need a go-to-market plan with AI assets and launch timing"

    quote_a = service.quote_for_prompt(prompt)
    quote_b = service.quote_for_prompt(prompt)

    assert quote_a.model_dump(mode="json") == quote_b.model_dump(mode="json")


def test_chat_plan_and_payment_intent_expose_quote_outputs(client: TestClient) -> None:
    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-1",
            "chat_session_id": "chat-1",
            "user_message": "Recommend a launch plan for a robotics demo",
        },
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["quote"]["official_quote_cents"] >= plan_payload["quote"]["runtime_cost_cents"]
    assert (
        plan_payload["quote"]["platform_fee_cents"] + plan_payload["quote"]["machine_share_cents"]
        == plan_payload["quote"]["official_quote_cents"]
    )

    machine = _create_machine(client)
    order = _create_order(client, machine_id=machine["id"], quoted_amount_cents=1250)
    intent_response = client.post(
        f"/api/v1/payments/orders/{order['id']}/intent",
        json={"amount_cents": 1250, "currency": "usdc"},
    )
    assert intent_response.status_code == 201
    intent_payload = intent_response.json()
    assert intent_payload["merchant_order_id"].startswith("merchant_")
    assert intent_payload["flow_id"].startswith("flow_")
    assert intent_payload["quote"] == {
        "runtime_cost_cents": 938,
        "official_quote_cents": 1250,
        "platform_fee_cents": 125,
        "machine_share_cents": 1125,
        "pwr_quote": "45.0000",
        "pwr_anchor_price_cents": 25,
        "currency": "USD",
        "pricing_version": "phase1_v3",
    }
