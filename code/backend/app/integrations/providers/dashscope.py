"""DashScope provider adapter for text, image, and video generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ...core.config import Settings, get_settings
from ...execution.contracts import MediaType
from .base import GenerationRequest, GenerationResponse, ProviderTaskStatus

_PROVIDER_NAME = "dashscope"
_TEXT_PATH = "/chat/completions"
_IMAGE_PATH = "/api/v1/services/aigc/image-generation/generation"
_VIDEO_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"


def _strip_trailing_slash(value: str) -> str:
    return value[:-1] if value.endswith("/") else value


@dataclass(frozen=True)
class DashScopeEndpointSet:
    compatible_base_url: str
    base_url: str


class DashScopeProviderAdapter:
    """Provider boundary that routes OutcomeX generation requests into DashScope."""

    provider_name = _PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        compatible_base_url: str,
        base_url: str,
        timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ):
        self._api_key = api_key
        self._endpoints = DashScopeEndpointSet(
            compatible_base_url=_strip_trailing_slash(compatible_base_url),
            base_url=_strip_trailing_slash(base_url),
        )
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "DashScopeProviderAdapter":
        resolved = settings or get_settings()
        return cls(
            api_key=resolved.dashscope_api_key,
            compatible_base_url=resolved.dashscope_compatible_base_url,
            base_url=resolved.dashscope_base_url,
            timeout_seconds=resolved.dashscope_request_timeout_seconds,
        )

    def submit_generation(self, request: GenerationRequest) -> GenerationResponse:
        if request.output_type == MediaType.TEXT:
            return self._submit_text(request)
        if request.output_type == MediaType.IMAGE:
            return self._submit_async_media(request, api_path=_IMAGE_PATH)
        if request.output_type == MediaType.VIDEO:
            return self._submit_async_media(request, api_path=_VIDEO_PATH)
        return GenerationResponse(
            success=False,
            provider=self.provider_name,
            status=ProviderTaskStatus.FAILED,
            error=f"unsupported_output_type:{request.output_type.value}",
        )

    def poll_generation(self, task_id: str, *, model_id: str, action: str) -> GenerationResponse:
        response = self._request(
            method="GET",
            url=f"{self._endpoints.base_url}/api/v1/tasks/{task_id}",
            headers=self._dashscope_headers(),
        )
        if not response["ok"]:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                task_id=task_id,
                error=response["error"],
            )

        payload = response["json"]
        output = payload.get("output", {})
        task_status = str(output.get("task_status", "")).upper()
        status = _map_task_status(task_status)
        result_urls = tuple(_collect_urls(output))
        return GenerationResponse(
            success=status != ProviderTaskStatus.FAILED,
            provider=self.provider_name,
            status=status,
            task_id=task_id,
            result_urls=result_urls,
            error=payload.get("message") if status == ProviderTaskStatus.FAILED else None,
            metadata={
                "request_id": str(payload.get("request_id", "")),
                "task_status": task_status.lower(),
                "model_id": model_id,
                "action": action,
            },
        )

    def _submit_text(self, request: GenerationRequest) -> GenerationResponse:
        payload = {
            "model": request.model_id,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.options:
            payload.update(request.options)
        response = self._request(
            method="POST",
            url=f"{self._endpoints.compatible_base_url}{_TEXT_PATH}",
            headers=self._compatible_headers(),
            json=payload,
        )
        if not response["ok"]:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                error=response["error"],
            )

        body = response["json"]
        content = _extract_text_content(body)
        finish_reason = _extract_finish_reason(body)
        return GenerationResponse(
            success=True,
            provider=self.provider_name,
            status=ProviderTaskStatus.SUCCEEDED,
            metadata={
                "text": content,
                "finish_reason": finish_reason,
                "request_id": str(body.get("id", body.get("request_id", ""))),
                "model_id": request.model_id,
            },
        )

    def _submit_async_media(self, request: GenerationRequest, *, api_path: str) -> GenerationResponse:
        parameters = dict(request.options)
        if request.negative_prompt:
            parameters.setdefault("negative_prompt", request.negative_prompt)
        payload = {
            "model": request.model_id,
            "input": {
                "prompt": request.prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": request.prompt}],
                    }
                ],
            },
        }
        if parameters:
            payload["parameters"] = parameters
        response = self._request(
            method="POST",
            url=f"{self._endpoints.base_url}{api_path}",
            headers=self._dashscope_headers(async_mode=True),
            json=payload,
        )
        if not response["ok"]:
            return GenerationResponse(
                success=False,
                provider=self.provider_name,
                status=ProviderTaskStatus.FAILED,
                error=response["error"],
            )

        body = response["json"]
        output = body.get("output", {})
        task_id = str(output.get("task_id", ""))
        return GenerationResponse(
            success=bool(task_id),
            provider=self.provider_name,
            status=ProviderTaskStatus.QUEUED if task_id else ProviderTaskStatus.FAILED,
            task_id=task_id or None,
            error=None if task_id else "missing_task_id",
            metadata={
                "request_id": str(body.get("request_id", "")),
                "task_status": str(output.get("task_status", "")).lower(),
                "model_id": request.model_id,
            },
        )

    def _compatible_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _dashscope_headers(self, *, async_mode: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if async_mode:
            headers["X-DashScope-Async"] = "enable"
        return headers

    def _request(self, *, method: str, url: str, headers: dict[str, str], json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.request(method=method, url=url, headers=headers, json=json)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"http_error:{exc.__class__.__name__}"}

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            message = payload.get("message") or payload.get("error", {}).get("message") or f"http_status:{response.status_code}"
            return {"ok": False, "error": str(message), "json": payload}

        return {"ok": True, "json": payload}


def _extract_text_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _extract_finish_reason(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("finish_reason", ""))


def _map_task_status(task_status: str) -> ProviderTaskStatus:
    if task_status in {"PENDING", "QUEUED"}:
        return ProviderTaskStatus.QUEUED
    if task_status in {"RUNNING"}:
        return ProviderTaskStatus.RUNNING
    if task_status in {"SUCCEEDED"}:
        return ProviderTaskStatus.SUCCEEDED
    return ProviderTaskStatus.FAILED


def _collect_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            urls.extend(_collect_urls(nested))
        return urls
    if isinstance(value, list):
        for nested in value:
            urls.extend(_collect_urls(nested))
        return urls
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return [value]
    return []
