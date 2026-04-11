#!/usr/bin/env python3
"""Generate images through an OutcomeX-controlled DashScope boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com"
DEFAULT_IMAGE_MODEL = "wan2.6-t2i"
IMAGE_PATH = "/api/v1/services/aigc/image-generation/generation"


def _require_requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - runtime-only path
        print(f"Error: requests is required: {exc}")
        sys.exit(1)
    return requests


def _strip_trailing_slash(value: str) -> str:
    return value[:-1] if value.endswith("/") else value


def _resolve_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    for key in ("DASHSCOPE_API_KEY", "OUTCOMEX_DASHSCOPE_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    print("Error: missing DashScope-compatible API key.")
    print("Set one of DASHSCOPE_API_KEY, OUTCOMEX_DASHSCOPE_API_KEY, OPENAI_API_KEY, or LLM_API_KEY.")
    sys.exit(1)


def _resolve_base_url() -> str:
    for key in ("DASHSCOPE_BASE_URL", "OUTCOMEX_DASHSCOPE_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL"):
        value = os.getenv(key, "").strip()
        if not value:
            continue
        if value.endswith("/compatible-mode/v1"):
            value = value[: -len("/compatible-mode/v1")]
        return _strip_trailing_slash(value)
    return DEFAULT_BASE_URL


def _resolve_model(requested_model: str | None) -> str:
    candidate = (
        requested_model
        or os.getenv("DASHSCOPE_IMAGE_MODEL")
        or os.getenv("OUTCOMEX_IMAGE_MODEL")
        or DEFAULT_IMAGE_MODEL
    )
    legacy_aliases = {
        "google/gemini-3-pro-image-preview": DEFAULT_IMAGE_MODEL,
        "black-forest-labs/flux.2-pro": DEFAULT_IMAGE_MODEL,
        "black-forest-labs/flux.2-flex": DEFAULT_IMAGE_MODEL,
    }
    return legacy_aliases.get(candidate, candidate)


def _headers(api_key: str, *, async_mode: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    return headers


def _collect_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            urls.extend(_collect_urls(nested))
    elif isinstance(value, list):
        for nested in value:
            urls.extend(_collect_urls(nested))
    elif isinstance(value, str) and value.startswith("http"):
        urls.append(value)
    return urls


def _download_image(image_url: str, output_path: str) -> None:
    requests = _require_requests()
    response = requests.get(image_url, timeout=120)
    response.raise_for_status()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def generate_image(
    prompt: str,
    model: str = DEFAULT_IMAGE_MODEL,
    output_path: str = "generated_image.png",
    api_key: str | None = None,
    input_image: str | None = None,
) -> dict[str, Any]:
    """Generate an image through DashScope's async image endpoint."""
    if input_image:
        raise RuntimeError("image_editing_not_supported_in_dashscope_script_boundary")

    requests = _require_requests()
    resolved_api_key = _resolve_api_key(api_key)
    resolved_base_url = _resolve_base_url()
    resolved_model = _resolve_model(model)

    response = requests.post(
        url=f"{resolved_base_url}{IMAGE_PATH}",
        headers=_headers(resolved_api_key, async_mode=True),
        json={
            "model": resolved_model,
            "input": {
                "prompt": prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ],
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    task_id = str(body.get("output", {}).get("task_id", "")).strip()
    if not task_id:
        raise RuntimeError(f"missing_task_id:{json.dumps(body, ensure_ascii=False)}")

    task_url = f"{resolved_base_url}/api/v1/tasks/{task_id}"
    status_payload: dict[str, Any] = {}
    for _ in range(90):
        status_response = requests.get(task_url, headers=_headers(resolved_api_key), timeout=60)
        status_response.raise_for_status()
        status_payload = status_response.json()
        task_status = str(status_payload.get("output", {}).get("task_status", "")).upper()
        if task_status == "SUCCEEDED":
            break
        if task_status in {"FAILED", "CANCELED"}:
            raise RuntimeError(f"image_generation_failed:{json.dumps(status_payload, ensure_ascii=False)}")
        time.sleep(2)
    else:
        raise RuntimeError("image_generation_timeout")

    urls = _collect_urls(status_payload.get("output", {}))
    if not urls:
        raise RuntimeError(f"missing_result_url:{json.dumps(status_payload, ensure_ascii=False)}")
    _download_image(urls[0], output_path)
    print(f"Image saved to: {output_path}")
    return status_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate images through a DashScope/OutcomeX provider boundary.",
    )
    parser.add_argument("prompt", type=str, help="Text description of the image to generate")
    parser.add_argument("--model", "-m", type=str, default=DEFAULT_IMAGE_MODEL, help="DashScope image model ID")
    parser.add_argument("--output", "-o", type=str, default="generated_image.png", help="Output file path")
    parser.add_argument("--input", "-i", type=str, help="Input image path for editing (unsupported in this boundary)")
    parser.add_argument("--api-key", type=str, help="DashScope-compatible API key override")

    args = parser.parse_args()
    try:
        generate_image(
            prompt=args.prompt,
            model=args.model,
            output_path=args.output,
            api_key=args.api_key,
            input_image=args.input,
        )
    except Exception as exc:  # pragma: no cover - CLI path
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
