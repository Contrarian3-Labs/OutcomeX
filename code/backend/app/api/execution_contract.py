from __future__ import annotations

from app.domain.models import Order


def merge_submission_payload(
    *,
    order: Order | None,
    persisted_payload: dict | None,
    snapshot_payload: dict | None,
) -> dict | None:
    payload: dict[str, object] = {}
    if persisted_payload:
        payload.update(dict(persisted_payload))
    if snapshot_payload:
        payload.update(dict(snapshot_payload))

    if order is not None:
        metadata = dict(order.execution_metadata or {})
        execution_request = dict(order.execution_request or {})
        if metadata.get("selected_plan_id"):
            payload.setdefault("selected_plan_id", metadata["selected_plan_id"])
        if metadata.get("selected_plan_strategy"):
            payload.setdefault("selected_plan_strategy", metadata["selected_plan_strategy"])
        if payload.get("selected_plan_index") is None and metadata.get("selected_native_plan_index") is not None:
            payload["selected_plan_index"] = metadata["selected_native_plan_index"]
        if payload.get("execution_strategy") is None and execution_request.get("execution_strategy") is not None:
            payload["execution_strategy"] = execution_request["execution_strategy"]
        if payload.get("files") in (None, []):
            payload["files"] = list(execution_request.get("files") or [])

    return payload or None


def build_selected_plan_payload(
    *,
    order: Order | None,
    snapshot_selected_plan: dict | None,
    submission_payload: dict | None = None,
) -> dict | None:
    if snapshot_selected_plan:
        payload = dict(snapshot_selected_plan)
    elif order is None:
        return None
    else:
        metadata = dict(order.execution_metadata or {})
        name = metadata.get("selected_native_plan_name")
        description = metadata.get("selected_native_plan_description")
        nodes = metadata.get("selected_native_plan_nodes")
        if not (name or description or nodes):
            return None
        payload = {
            "index": metadata.get("selected_native_plan_index"),
            "name": name,
            "description": description,
            "nodes": nodes or [],
        }

    merged_payload = submission_payload or {}
    if payload.get("index") is None:
        if merged_payload.get("selected_plan_index") is not None:
            payload["index"] = merged_payload.get("selected_plan_index")
        elif order is not None:
            payload["index"] = dict(order.execution_metadata or {}).get("selected_native_plan_index")
    return payload


def build_selected_plan_binding(
    *,
    order: Order | None,
    selected_plan: dict | None,
    submission_payload: dict | None,
) -> dict | None:
    metadata = dict(order.execution_metadata or {}) if order is not None else {}
    execution_request = dict(order.execution_request or {}) if order is not None else {}
    payload = dict(submission_payload or {})

    order_selected_plan_id = metadata.get("selected_plan_id")
    order_selected_plan_strategy = metadata.get("selected_plan_strategy")
    order_selected_plan_index = metadata.get("selected_native_plan_index")
    order_input_files = list(execution_request.get("files") or [])

    submission_payload_selected_plan_id = payload.get("selected_plan_id")
    submission_payload_execution_strategy = payload.get("execution_strategy")
    submission_payload_files = list(payload.get("files") or [])
    submission_payload_selected_plan_index = payload.get("selected_plan_index")

    selected_plan_index = selected_plan.get("index") if selected_plan else None
    selected_plan_name = selected_plan.get("name") if selected_plan else None

    if (
        order_selected_plan_id is None
        and order_selected_plan_strategy is None
        and order_selected_plan_index is None
        and selected_plan_index is None
        and submission_payload_selected_plan_index is None
        and not order_input_files
        and not submission_payload_files
    ):
        return None

    selected_plan_strategy_matches = order_selected_plan_strategy == submission_payload_execution_strategy
    input_files_match = order_input_files == submission_payload_files
    selected_plan_id_present = bool(order_selected_plan_id)
    selected_plan_id_matches = (
        submission_payload_selected_plan_id == order_selected_plan_id if order_selected_plan_id else False
    )
    selected_plan_index_matches = (
        submission_payload_selected_plan_index == order_selected_plan_index == selected_plan_index
    )

    return {
        "order_selected_plan_id": order_selected_plan_id,
        "order_selected_plan_strategy": order_selected_plan_strategy,
        "order_selected_plan_index": order_selected_plan_index,
        "order_input_files": order_input_files,
        "submission_payload_selected_plan_id": submission_payload_selected_plan_id,
        "submission_payload_execution_strategy": submission_payload_execution_strategy,
        "submission_payload_files": submission_payload_files,
        "submission_payload_selected_plan_index": submission_payload_selected_plan_index,
        "selected_plan_index": selected_plan_index,
        "selected_plan_name": selected_plan_name,
        "selected_plan_strategy_matches": selected_plan_strategy_matches,
        "input_files_match": input_files_match,
        "selected_plan_id_present": selected_plan_id_present,
        "is_consistent": bool(
            selected_plan_id_present
            and selected_plan_id_matches
            and selected_plan_index_matches
            and selected_plan_strategy_matches
            and input_files_match
        ),
    }


def is_order_execution_contract_consistent(order: Order) -> bool:
    metadata = dict(order.execution_metadata or {})
    execution_request = dict(order.execution_request or {})

    selected_plan_id = metadata.get("selected_plan_id")
    selected_plan_strategy = metadata.get("selected_plan_strategy")
    selected_plan_index = metadata.get("selected_native_plan_index")
    request_strategy = execution_request.get("execution_strategy")
    request_files = execution_request.get("files")

    if not selected_plan_id or not selected_plan_strategy or selected_plan_index is None:
        return False
    if request_strategy != selected_plan_strategy:
        return False
    if request_files is None:
        return False
    return True
