import json

import httpx

from app.execution.contracts import MediaType
from app.integrations.providers.base import GenerationRequest, ProviderTaskStatus
from app.integrations.providers.dashscope import DashScopeProviderAdapter


def test_dashscope_text_submission_uses_compatible_chat_endpoint() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "hello from qwen"},
                    }
                ],
            },
        )

    adapter = DashScopeProviderAdapter(
        api_key="test-key",
        compatible_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url="https://dashscope-intl.aliyuncs.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.submit_generation(
        GenerationRequest(
            prompt="Say hello",
            output_type=MediaType.TEXT,
            model_id="qwen3.6-plus",
        )
    )

    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "qwen3.6-plus"
    assert result.success is True
    assert result.status == ProviderTaskStatus.SUCCEEDED
    assert result.metadata["text"] == "hello from qwen"


def test_dashscope_image_submission_returns_async_task() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["async_header"] = request.headers.get("X-DashScope-Async")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "request_id": "req-image-1",
                "output": {
                    "task_id": "task-image-1",
                    "task_status": "PENDING",
                },
            },
        )

    adapter = DashScopeProviderAdapter(
        api_key="test-key",
        compatible_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url="https://dashscope-intl.aliyuncs.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.submit_generation(
        GenerationRequest(
            prompt="Generate a poster",
            output_type=MediaType.IMAGE,
            model_id="wan2.6-t2i",
        )
    )

    assert captured["url"].endswith("/api/v1/services/aigc/image-generation/generation")
    assert captured["async_header"] == "enable"
    assert captured["body"]["model"] == "wan2.6-t2i"
    assert result.success is True
    assert result.status == ProviderTaskStatus.QUEUED
    assert result.task_id == "task-image-1"


def test_dashscope_poll_extracts_media_urls() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "req-poll-1",
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {"url": "https://cdn.example.com/render-1.png"},
                        {"url": "https://cdn.example.com/render-2.png"},
                    ],
                },
            },
        )

    adapter = DashScopeProviderAdapter(
        api_key="test-key",
        compatible_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url="https://dashscope-intl.aliyuncs.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.poll_generation(
        "task-image-1",
        model_id="wan2.6-t2i",
        action="generation",
    )

    assert result.success is True
    assert result.status == ProviderTaskStatus.SUCCEEDED
    assert result.result_urls == (
        "https://cdn.example.com/render-1.png",
        "https://cdn.example.com/render-2.png",
    )
