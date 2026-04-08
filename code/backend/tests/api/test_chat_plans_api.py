import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "chat-plans.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as test_client:
        yield test_client

    reset_settings_cache()
    reset_container_cache()


def _create_machine(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/machines",
        json={"display_name": "GANA node", "owner_user_id": "owner-1"},
    )
    assert response.status_code == 201
    return response.json()


def test_chat_plans_returns_three_productsized_recommendations(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-1",
            "chat_session_id": "chat-1",
            "user_message": "Create a launch-ready teaser campaign with visual assets",
            "mode": "efficiency",
            "input_files": ["brief.pdf", "brand-guide.png"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "efficiency"
    assert payload["input_files"] == ["brief.pdf", "brand-guide.png"]
    assert payload["recommended_plan_summary"]
    assert [plan["strategy"] for plan in payload["recommended_plans"]] == ["efficiency", "quality", "simplicity"]
    assert [plan["native_plan_index"] for plan in payload["recommended_plans"]] == [1, 0, 2]
    assert all(plan["plan_id"] for plan in payload["recommended_plans"])
    assert all(plan["title"] for plan in payload["recommended_plans"])
    assert all(plan["summary"] for plan in payload["recommended_plans"])
    assert all(plan["why_this_plan"] for plan in payload["recommended_plans"])
    assert all(plan["tradeoff"] for plan in payload["recommended_plans"])
    assert all(plan["native_plan_name"] for plan in payload["recommended_plans"])
    assert all(plan["native_plan_description"] for plan in payload["recommended_plans"])
    assert payload["recommended_plan_summary"] == payload["recommended_plans"][0]["summary"]


def test_order_creation_binds_selected_plan_id_to_execution_request(client: TestClient) -> None:
    machine = _create_machine(client)
    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-1",
            "chat_session_id": "chat-1",
            "user_message": "Generate a campaign brief and teaser assets",
        },
    )
    assert plan_response.status_code == 200
    selected_plan = next(
        plan for plan in plan_response.json()["recommended_plans"] if plan["strategy"] == "simplicity"
    )

    order_response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Generate a campaign brief and teaser assets",
            "quoted_amount_cents": 1000,
            "selected_plan_id": selected_plan["plan_id"],
            "input_files": ["brief.md"],
        },
    )

    assert order_response.status_code == 201
    payload = order_response.json()
    assert payload["recommended_plan_summary"] == selected_plan["summary"]
    assert payload["execution_request"]["execution_strategy"] == "simplicity"
    assert payload["execution_metadata"]["selected_plan_id"] == selected_plan["plan_id"]
    assert payload["execution_metadata"]["selected_plan_strategy"] == "simplicity"
    assert payload["execution_metadata"]["selected_native_plan_index"] == 2


def test_order_creation_rejects_unknown_selected_plan_id(client: TestClient) -> None:
    machine = _create_machine(client)

    order_response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-1",
            "machine_id": machine["id"],
            "chat_session_id": "chat-1",
            "user_prompt": "Generate a campaign brief and teaser assets",
            "quoted_amount_cents": 1000,
            "selected_plan_id": "plan_invalid",
        },
    )

    assert order_response.status_code == 409
    assert order_response.json()["detail"] == "Selected plan is invalid for this request"
