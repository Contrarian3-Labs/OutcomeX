from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import chat_plans as chat_plans_route
from app.api.routes import orders as orders_route
from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.domain.planning import RecommendedPlan
from app.execution.contracts import ExecutionStrategy
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "chat-plans.db"
    monkeypatch.setenv("OUTCOMEX_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("OUTCOMEX_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("OUTCOMEX_DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("OUTCOMEX_AGENTSKILLOS_ROOT", "")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_INDEXER_ENABLED", "false")
    monkeypatch.setenv("OUTCOMEX_ONCHAIN_RPC_URL", "")
    monkeypatch.setenv(
        "OUTCOMEX_BUYER_WALLET_MAP_JSON",
        '{"buyer-1":"0x1111111111111111111111111111111111111111"}',
    )
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


def _issue_attachment_session(client: TestClient) -> dict:
    response = client.post("/api/v1/attachments/sessions")
    assert response.status_code == 201
    return response.json()


def _upload_attachment(client: TestClient, *, session_id: str, session_token: str) -> str:
    response = client.post(
        "/api/v1/attachments",
        data={"session_id": session_id},
        headers={"X-Attachment-Session-Token": session_token},
        files={"file": ("brief.txt", b"creative brief payload", "text/plain")},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _recommended_plans_for_test(preferred_strategy: ExecutionStrategy | None) -> tuple[RecommendedPlan, ...]:
    plans = (
        RecommendedPlan(
            plan_id="plan-quality",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.QUALITY,
            title="Quality",
            summary="Quality path",
            why_this_plan="For quality",
            tradeoff="Slower",
            native_plan_index=0,
            native_plan_name="Quality",
            native_plan_description="Quality path",
        ),
        RecommendedPlan(
            plan_id="plan-efficiency",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.EFFICIENCY,
            title="Efficiency",
            summary="Efficiency path",
            why_this_plan="For speed",
            tradeoff="Less depth",
            native_plan_index=1,
            native_plan_name="Efficiency",
            native_plan_description="Efficiency path",
        ),
        RecommendedPlan(
            plan_id="plan-simplicity",
            context_digest="ctx_test",
            strategy=ExecutionStrategy.SIMPLICITY,
            title="Simplicity",
            summary="Simplicity path",
            why_this_plan="For lean flow",
            tradeoff="Least checks",
            native_plan_index=2,
            native_plan_name="Simplicity",
            native_plan_description="Simplicity path",
        ),
    )
    if preferred_strategy is None:
        return plans
    preferred = [plan for plan in plans if plan.strategy == preferred_strategy]
    remaining = [plan for plan in plans if plan.strategy != preferred_strategy]
    return tuple(preferred + remaining)


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


def test_chat_plans_canonicalizes_wallet_address_user_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "0x1111111111111111111111111111111111111111",
            "chat_session_id": "chat-wallet-user",
            "user_message": "Create a launch-ready teaser campaign with visual assets",
        },
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "buyer-1"


def test_order_creation_binds_selected_plan_id_to_execution_request(client: TestClient) -> None:
    machine = _create_machine(client)
    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-1",
            "chat_session_id": "chat-1",
            "user_message": "Generate a campaign brief and teaser assets",
            "input_files": ["brief.md"],
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
            "planning_context_id": plan_response.json()["planning_context_id"],
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


def test_benchmark_web_solutions_prefer_simplicity_native_plan_by_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _create_machine(client)

    def _stub_build_recommended_plans(  # noqa: PLR0913
        *,
        user_id: str,
        chat_session_id: str,
        user_message: str,
        preferred_strategy: ExecutionStrategy | None,
        input_files: tuple[str, ...],
        planning_context_key: str = "",
    ) -> tuple[RecommendedPlan, ...]:
        assert preferred_strategy == ExecutionStrategy.SIMPLICITY
        return _recommended_plans_for_test(ExecutionStrategy.QUALITY)

    monkeypatch.setattr(chat_plans_route, "build_recommended_plans", _stub_build_recommended_plans)
    monkeypatch.setattr(orders_route, "build_recommended_plans", _stub_build_recommended_plans)

    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-benchmark",
            "chat_session_id": "chat-benchmark",
            "user_message": "ignored by benchmark task prompt",
            "benchmark_task_id": "web_interaction_task1",
            "mode": "quality",
        },
    )

    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["recommended_plans"][0]["strategy"] == "simplicity"
    assert plan_payload["recommended_plans"][0]["native_plan_index"] == 2

    order_response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-benchmark",
            "machine_id": machine["id"],
            "chat_session_id": "chat-benchmark",
            "user_prompt": "ignored by benchmark task prompt",
            "benchmark_task_id": "web_interaction_task1",
            "quoted_amount_cents": 1000,
            "planning_context_id": plan_payload["planning_context_id"],
        },
    )

    assert order_response.status_code == 201
    order_payload = order_response.json()
    assert order_payload["execution_request"]["execution_strategy"] == "simplicity"
    assert order_payload["execution_metadata"]["selected_native_plan_index"] == 2
    assert order_payload["execution_metadata"]["benchmark_solution_title"] == "Competitor Website Analysis Report"


def test_chat_plans_resolve_uploaded_attachments_to_real_paths_for_planning(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _issue_attachment_session(client)
    attachment_id = _upload_attachment(
        client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )
    captured_path: dict[str, Path] = {}

    def _stub_build_recommended_plans(  # noqa: PLR0913
        *,
        user_id: str,
        chat_session_id: str,
        user_message: str,
        preferred_strategy: ExecutionStrategy | None,
        input_files: tuple[str, ...],
        planning_context_key: str = "",
    ) -> tuple[RecommendedPlan, ...]:
        assert user_id == "user-attachment"
        assert chat_session_id == "chat-attachment"
        assert user_message == "Plan with uploaded context"
        assert len(input_files) == 1
        assert planning_context_key.startswith("ctx_")
        resolved = Path(input_files[0])
        assert resolved.exists()
        assert resolved.read_bytes() == b"creative brief payload"
        captured_path["value"] = resolved
        return _recommended_plans_for_test(preferred_strategy)

    monkeypatch.setattr(chat_plans_route, "build_recommended_plans", _stub_build_recommended_plans)

    response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-attachment",
            "chat_session_id": "chat-attachment",
            "user_message": "Plan with uploaded context",
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )

    assert response.status_code == 200
    assert response.json()["input_files"] == []
    assert response.json()["attachment_session_id"] == session["session_id"]
    assert response.json()["attachment_ids"] == [attachment_id]
    assert response.json()["planning_context_id"].startswith("ctx_")
    assert "value" in captured_path
    assert not captured_path["value"].exists()


def test_chat_plans_context_id_and_plan_id_change_with_different_attachment_refs(client: TestClient) -> None:
    session = _issue_attachment_session(client)
    attachment_one = _upload_attachment(
        client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )
    second_upload = client.post(
        "/api/v1/attachments",
        data={"session_id": session["session_id"]},
        headers={"X-Attachment-Session-Token": session["session_token"]},
        files={"file": ("style-guide.txt", b"brand v2", "text/plain")},
    )
    assert second_upload.status_code == 201
    attachment_two = str(second_upload.json()["id"])

    payload = {
        "user_id": "user-context",
        "chat_session_id": "chat-context",
        "user_message": "Plan this with attachment context",
    }
    first_response = client.post(
        "/api/v1/chat/plans",
        json={
            **payload,
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_one],
        },
    )
    second_response = client.post(
        "/api/v1/chat/plans",
        json={
            **payload,
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_two],
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["attachment_session_id"] == session["session_id"]
    assert second_payload["attachment_session_id"] == session["session_id"]
    assert first_payload["attachment_ids"] == [attachment_one]
    assert second_payload["attachment_ids"] == [attachment_two]
    assert first_payload["planning_context_id"].startswith("ctx_")
    assert second_payload["planning_context_id"].startswith("ctx_")
    assert first_payload["planning_context_id"] != second_payload["planning_context_id"]
    assert first_payload["recommended_plans"][0]["plan_id"] != second_payload["recommended_plans"][0]["plan_id"]


def test_order_creation_preserves_attachment_context_from_selected_plan(client: TestClient) -> None:
    machine = _create_machine(client)
    session = _issue_attachment_session(client)
    attachment_id = _upload_attachment(
        client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )

    plan_response = client.post(
        "/api/v1/chat/plans",
        json={
            "user_id": "user-order-attachment",
            "chat_session_id": "chat-order-attachment",
            "user_message": "Create deliverable from uploaded brief",
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )
    assert plan_response.status_code == 200
    selected_plan = plan_response.json()["recommended_plans"][0]

    order_response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-order-attachment",
            "machine_id": machine["id"],
            "chat_session_id": "chat-order-attachment",
            "user_prompt": "Create deliverable from uploaded brief",
            "quoted_amount_cents": 1000,
            "selected_plan_id": selected_plan["plan_id"],
            "planning_context_id": plan_response.json()["planning_context_id"],
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )

    assert order_response.status_code == 201
    payload = order_response.json()
    assert payload["execution_request"]["planning_context_id"] == plan_response.json()["planning_context_id"]
    assert payload["execution_request"]["attachment_session_id"] == session["session_id"]
    assert payload["execution_request"]["attachment_ids"] == [attachment_id]
    assert payload["execution_metadata"]["planning_context_id"] == plan_response.json()["planning_context_id"]
    assert payload["execution_metadata"]["attachment_session_id"] == session["session_id"]
    assert payload["execution_metadata"]["attachment_ids"] == [attachment_id]


def test_order_creation_rejects_mismatched_planning_context(client: TestClient) -> None:
    machine = _create_machine(client)
    session = _issue_attachment_session(client)
    attachment_id = _upload_attachment(
        client,
        session_id=session["session_id"],
        session_token=session["session_token"],
    )

    response = client.post(
        "/api/v1/orders",
        json={
            "user_id": "user-order-mismatch",
            "machine_id": machine["id"],
            "chat_session_id": "chat-order-mismatch",
            "user_prompt": "Create deliverable from uploaded brief",
            "quoted_amount_cents": 1000,
            "planning_context_id": "ctx_wrong",
            "attachment_session_id": session["session_id"],
            "attachment_session_token": session["session_token"],
            "attachment_ids": [attachment_id],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "planning_context_id does not match input_files/attachment references"
