#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com"
DEFAULT_MODEL = "wan2.7-videoedit"
ENDPOINT = "/api/v1/services/aigc/video-generation/video-synthesis"


def _require_requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        print(f"Error: requests is required: {exc}")
        sys.exit(1)
    return requests


def _resolve_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    for key in ("DASHSCOPE_API_KEY", "OUTCOMEX_DASHSCOPE_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    raise RuntimeError("missing_dashscope_api_key")


def _resolve_base_url() -> str:
    for key in ("DASHSCOPE_BASE_URL", "OUTCOMEX_DASHSCOPE_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL"):
        value = os.getenv(key, "").strip()
        if value:
            return value.removesuffix("/compatible-mode/v1").rstrip("/")
    return DEFAULT_BASE_URL


def _headers(api_key: str, *, async_mode: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    return headers


def _to_asset_ref(value: str) -> str:
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


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


def _download(url: str, output: str) -> None:
    requests = _require_requests()
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def main() -> int:
    parser = argparse.ArgumentParser(description="Edit an existing video with DashScope Wan VideoEdit.")
    parser.add_argument("prompt", help="Editing instruction")
    parser.add_argument("--input", "-i", required=True, help="Input video path or URL")
    parser.add_argument("--output", "-o", default="edited_video.mp4", help="Output video path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="DashScope model")
    parser.add_argument("--resolution", default="720P", help="Output resolution")
    parser.add_argument("--api-key", help="API key override")
    args = parser.parse_args()

    requests = _require_requests()
    api_key = _resolve_api_key(args.api_key)
    base_url = _resolve_base_url()
    payload = {
        "model": args.model,
        "input": {
            "prompt": args.prompt,
            "media": [{"type": "video", "url": _to_asset_ref(args.input)}],
        },
        "parameters": {
            "resolution": args.resolution,
            "prompt_extend": False,
            "watermark": False,
        },
    }

    response = requests.post(
        f"{base_url}{ENDPOINT}",
        headers=_headers(api_key, async_mode=True),
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    body = response.json()
    task_id = str(body.get("output", {}).get("task_id", "")).strip()
    if not task_id:
        raise RuntimeError(f"missing_task_id:{body}")

    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    status_payload: dict[str, Any] = {}
    for _ in range(120):
        status_response = requests.get(task_url, headers=_headers(api_key), timeout=60)
        status_response.raise_for_status()
        status_payload = status_response.json()
        task_status = str(status_payload.get("output", {}).get("task_status", "")).upper()
        if task_status == "SUCCEEDED":
            break
        if task_status in {"FAILED", "CANCELED"}:
            raise RuntimeError(f"video_edit_failed:{status_payload}")
        time.sleep(5)
    else:
        raise RuntimeError("video_edit_timeout")

    urls = _collect_urls(status_payload.get("output", {}))
    if not urls:
        raise RuntimeError(f"missing_result_url:{status_payload}")
    _download(urls[0], args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
