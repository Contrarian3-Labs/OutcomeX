"""Runtime client facade with Claude SDK and OpenAI-compatible backends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from openai import AsyncOpenAI

from orchestrator.runtime.models import SDKMetrics

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
except ImportError:  # pragma: no cover - optional runtime dependency
    AssistantMessage = ClaudeAgentOptions = ClaudeSDKClient = ResultMessage = None
    SystemMessage = TextBlock = ToolResultBlock = ToolUseBlock = UserMessage = None


_MAX_TOOL_OUTPUT_CHARS = 12000
_DEFAULT_RUNTIME_MODEL = "qwen3.6-plus"
_DEFAULT_COMPATIBLE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_DEFAULT_OPENAI_TOOL_MAX_TURNS = 48


def _truncate_output(value: str, *, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


def _normalize_model_name(model: str) -> str:
    value = (model or "").strip()
    if value.startswith("openai/"):
        return value.split("/", 1)[1]
    if value:
        return value
    return _DEFAULT_RUNTIME_MODEL


def _resolve_runtime_backend() -> str:
    explicit = os.getenv("AGENTSKILLOS_RUNTIME_BACKEND", "").strip().lower()
    if explicit in {"claude", "openai"}:
        return explicit

    if os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "claude"


def _resolve_openai_model(model: str) -> str:
    candidates = [
        os.getenv("AGENTSKILLOS_RUNTIME_MODEL", "").strip(),
        model.strip(),
        os.getenv("LLM_MODEL", "").strip(),
        os.getenv("OPENAI_MODEL", "").strip(),
        os.getenv("ANTHROPIC_MODEL", "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            return _normalize_model_name(candidate)
    return _DEFAULT_RUNTIME_MODEL


def _resolve_openai_base_url() -> str:
    candidates = [
        os.getenv("LLM_BASE_URL", "").strip(),
        os.getenv("OPENAI_BASE_URL", "").strip(),
        _DEFAULT_COMPATIBLE_BASE_URL,
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return _DEFAULT_COMPATIBLE_BASE_URL


def _resolve_openai_api_key() -> str:
    return os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()


def _resolve_openai_tool_max_turns() -> int:
    raw_value = os.getenv("AGENTSKILLOS_OPENAI_TOOL_MAX_TURNS", "").strip()
    if raw_value:
        try:
            parsed = int(raw_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return _DEFAULT_OPENAI_TOOL_MAX_TURNS


@dataclass
class _ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


class _LocalToolExecutor:
    def __init__(self, *, cwd: str, log_callback: Optional[Callable[[str, str], None]] = None):
        self.cwd = Path(cwd).resolve()
        self.log_callback = log_callback

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{tool_name.lower()}", None)
        if handler is None:
            return json.dumps({"error": f"unsupported_tool:{tool_name}"})

        try:
            result = handler(arguments)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            result = {"error": f"{exc.__class__.__name__}: {exc}"}
        return json.dumps(result, ensure_ascii=False)

    def _resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.cwd / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate != self.cwd and self.cwd not in candidate.parents:
            raise ValueError(f"path_outside_workdir:{candidate}")
        return candidate

    def _tool_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(str(arguments["file_path"]))
        offset = max(1, int(arguments.get("offset", 1)))
        limit = max(1, min(1000, int(arguments.get("limit", 200))))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        rendered = "\n".join(f"{offset + index}: {line}" for index, line in enumerate(selected))
        return {
            "file_path": str(path),
            "line_count": len(lines),
            "content": _truncate_output(rendered),
        }

    def _tool_write(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(str(arguments["file_path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(arguments.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return {"file_path": str(path), "bytes_written": len(content.encode("utf-8"))}

    def _tool_edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(str(arguments["file_path"]))
        old_string = str(arguments.get("old_string", ""))
        new_string = str(arguments.get("new_string", ""))
        replace_all = bool(arguments.get("replace_all", False))
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            return {"file_path": str(path), "error": "old_string_not_found"}
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        path.write_text(updated, encoding="utf-8")
        return {
            "file_path": str(path),
            "replacements": occurrences if replace_all else 1,
        }

    def _tool_glob(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments["pattern"])
        matches = sorted(self.cwd.glob(pattern))
        return {
            "pattern": pattern,
            "matches": [str(match) for match in matches[:200]],
            "truncated": len(matches) > 200,
        }

    def _tool_grep(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments["pattern"])
        search_root = self._resolve_path(str(arguments.get("path", ".")))
        if shutil.which("rg"):
            cmd = ["rg", "-n", "--no-heading", pattern, str(search_root)]
        else:
            cmd = ["grep", "-RIn", pattern, str(search_root)]
        proc = subprocess.run(cmd, cwd=self.cwd, capture_output=True, text=True, timeout=30)
        output = proc.stdout if proc.returncode in {0, 1} else proc.stderr
        return {
            "pattern": pattern,
            "path": str(search_root),
            "matches": _truncate_output(output.strip()),
            "return_code": proc.returncode,
        }

    def _tool_bash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = str(arguments["command"])
        proc = subprocess.run(
            command,
            cwd=self.cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "command": command,
            "stdout": _truncate_output(proc.stdout),
            "stderr": _truncate_output(proc.stderr),
            "return_code": proc.returncode,
        }

    def _tool_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        skill_name = str(arguments["name"])
        skill_dir = (self.cwd / ".claude" / "skills" / skill_name).resolve()
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return {"name": skill_name, "error": "skill_not_found"}
        files = sorted(
            str(path.relative_to(skill_dir))
            for path in skill_dir.rglob("*")
            if path.is_file()
        )
        return {
            "name": skill_name,
            "path": str(skill_dir),
            "files": files[:200],
            "skill_md": _truncate_output(skill_md.read_text(encoding="utf-8", errors="replace"), limit=8000),
        }


class _OpenAISessionClient:
    DEFAULT_ALLOWED_TOOLS = ["Skill", "Bash", "Read", "Write", "Glob", "Grep", "Edit"]
    DEFAULT_DISALLOWED_TOOLS = ["WebSearch", "WebFetch", "AskUserQuestion"]

    def __init__(
        self,
        *,
        session_id: str,
        allowed_tools: Optional[list[str]],
        disallowed_tools: Optional[list[str]],
        cwd: Optional[str],
        log_callback: Optional[Callable[[str, str], None]],
        model: str,
    ):
        self.session_id = session_id
        self.log_callback = log_callback
        self.cwd = cwd or str(Path.cwd())
        self.model = _resolve_openai_model(model)
        self.base_url = _resolve_openai_base_url()
        self.api_key = _resolve_openai_api_key()
        self.client = AsyncOpenAI(api_key=self.api_key or "missing", base_url=self.base_url)
        allowed = allowed_tools or list(self.DEFAULT_ALLOWED_TOOLS)
        banned = set(self.DEFAULT_DISALLOWED_TOOLS + (disallowed_tools or []))
        self.allowed_tools = [tool for tool in allowed if tool not in banned]
        self._tool_executor = _LocalToolExecutor(cwd=self.cwd, log_callback=log_callback)
        self._messages: list[dict[str, Any]] = []
        self._connected = False
        self.last_result_metrics: Optional[SDKMetrics] = None

    def _log(self, message: str, level: str = "info") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    async def connect(self, initial_prompt: Optional[str] = None) -> None:
        if not self.api_key:
            raise RuntimeError("openai_api_key_missing")
        self._connected = True
        self._messages = []
        if initial_prompt:
            self._messages.append({"role": "user", "content": initial_prompt})

    def _tool_specs(self) -> list[_ToolSpec]:
        specs = {
            "Read": _ToolSpec(
                name="Read",
                description="Read a file from the working directory. Use offset and limit for large files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["file_path"],
                },
            ),
            "Write": _ToolSpec(
                name="Write",
                description="Write full content to a file in the working directory.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
            ),
            "Edit": _ToolSpec(
                name="Edit",
                description="Replace text in a file. Use replace_all only when every match should change.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["file_path", "old_string", "new_string"],
                },
            ),
            "Glob": _ToolSpec(
                name="Glob",
                description="Find files under the working directory using a glob pattern.",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            ),
            "Grep": _ToolSpec(
                name="Grep",
                description="Search file contents using ripgrep or grep.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            ),
            "Bash": _ToolSpec(
                name="Bash",
                description="Run a shell command inside the working directory.",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            ),
            "Skill": _ToolSpec(
                name="Skill",
                description="Read a skill's SKILL.md and list its local files from .claude/skills/<name>.",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
        }
        return [specs[name] for name in self.allowed_tools if name in specs]

    def _openai_tools_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tool_specs()
        ]

    async def _run_completion_loop(self) -> str:
        start = time.time()
        total_input_tokens = 0
        total_output_tokens = 0
        total_turns = 0

        while True:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                tools=self._openai_tools_payload() or None,
                tool_choice="auto" if self.allowed_tools else None,
            )
            total_turns += 1
            usage = response.usage
            if usage is not None:
                total_input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                total_output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

            message = response.choices[0].message
            tool_calls = list(message.tool_calls or [])

            if tool_calls:
                assistant_message = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                        for tool_call in tool_calls
                    ],
                }
                self._messages.append(assistant_message)

                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments or "{}")
                    self._log(f"Tool: {tool_name}", "tool")
                    self._log(f"  Input: {arguments}", "info")
                    tool_result = self._tool_executor.execute(tool_name, arguments)
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
                    self._log(f"Tool Result ({tool_call.id}): {tool_result}", "info")
                if total_turns >= _resolve_openai_tool_max_turns():
                    raise RuntimeError("openai_tool_loop_exceeded_max_turns")
                continue

            text = message.content or ""
            self._messages.append({"role": "assistant", "content": text})
            self._log(text, "recv")
            self.last_result_metrics = SDKMetrics(
                duration_ms=int((time.time() - start) * 1000),
                total_cost_usd=0.0,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                num_turns=total_turns,
                is_error=False,
                subtype="openai_tool_loop",
            )
            self._log("Execution completed (cost: $0.0)", "ok")
            return text

    async def execute(self, prompt: str) -> str:
        self.last_result_metrics = None
        if not self._connected:
            await self.connect()
        self._log(f"Sending Query:\n{prompt}", "send")
        self._messages.append({"role": "user", "content": prompt})
        return await self._run_completion_loop()

    async def execute_with_metrics(self, prompt: str) -> tuple[str, dict[str, Any]]:
        text = await self.execute(prompt)
        metrics = self.last_result_metrics.to_dict() if self.last_result_metrics else {}
        return text, {
            "input_tokens": int(metrics.get("input_tokens", 0)),
            "output_tokens": int(metrics.get("output_tokens", 0)),
            "cost_usd": float(metrics.get("total_cost_usd", 0.0)),
        }

    async def stream_execute(self, prompt: str) -> AsyncIterator[str]:
        text = await self.execute(prompt)
        yield text

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class _ClaudeSessionClient:
    DEFAULT_DISALLOWED_TOOLS = ["WebSearch", "WebFetch", "AskUserQuestion"]

    def __init__(
        self,
        *,
        session_id: str,
        allowed_tools: Optional[list[str]],
        disallowed_tools: Optional[list[str]],
        cwd: Optional[str],
        log_callback: Optional[Callable[[str, str], None]],
        model: str,
    ):
        if ClaudeSDKClient is None:
            raise RuntimeError("claude_agent_sdk_missing")

        self.session_id = session_id
        self.log_callback = log_callback
        needs_skills = allowed_tools is None or "Skill" in allowed_tools
        final_disallowed = self.DEFAULT_DISALLOWED_TOOLS + (disallowed_tools or [])

        extra_args = {}
        if not needs_skills:
            extra_args["disable-slash-commands"] = None

        self.options = ClaudeAgentOptions(
            allowed_tools=allowed_tools or ["Skill", "Bash", "Read", "Write", "Glob", "Grep", "Edit"],
            disallowed_tools=final_disallowed,
            setting_sources=["project"] if needs_skills else [],
            permission_mode="default",
            cwd=cwd or str(Path.cwd()),
            max_buffer_size=10485760,
            model=model,
            extra_args=extra_args,
        )
        self.client: Optional[ClaudeSDKClient] = None
        self._connected = False
        self.last_result_metrics: Optional[SDKMetrics] = None

    def _log(self, message: str, level: str = "info") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    async def connect(self, initial_prompt: Optional[str] = None) -> None:
        self.client = ClaudeSDKClient(self.options)
        await self.client.connect(initial_prompt)
        self._connected = True

    async def execute(self, prompt: str) -> str:
        self.last_result_metrics = None
        if not self._connected or not self.client:
            await self.connect()

        self._log(f"Sending Query:\n{prompt}", "send")
        response_text = ""
        await self.client.query(prompt, session_id=self.session_id)

        async for message in self.client.receive_messages():
            if isinstance(message, UserMessage):
                if isinstance(message.content, str):
                    self._log(f"User Query: {message.content}", "send")
                else:
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            tool_id = getattr(block, "tool_use_id", "unknown")
                            self._log(f"Tool Result ({tool_id}): {block.content}", "info")
                        elif isinstance(block, TextBlock):
                            self._log(f"User Text: {block.text}", "info")
                        else:
                            self._log(f"{type(block).__name__}: {block}", "info")
            elif isinstance(message, AssistantMessage):
                if message.error:
                    self._log(f"Assistant ERROR: {message.error}", "error")
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                        self._log(block.text, "recv")
                    elif isinstance(block, ToolUseBlock):
                        self._log(f"Tool: {block.name}", "tool")
                        self._log(f"  Input: {block.input}", "info")
            elif isinstance(message, SystemMessage):
                self._log(f"System [{message.subtype}]: {message.data}", "info")
            elif isinstance(message, ResultMessage):
                self.last_result_metrics = SDKMetrics.from_result_message(message)
                self._log(f"Execution completed (cost: ${self.last_result_metrics.total_cost_usd})", "ok")
                break
            else:
                self._log(f"Unknown message type: {type(message).__name__} - {message}", "warn")

        return response_text

    async def execute_with_metrics(self, prompt: str) -> tuple[str, dict[str, Any]]:
        text = await self.execute(prompt)
        metrics = self.last_result_metrics.to_dict() if self.last_result_metrics else {}
        return text, {
            "input_tokens": int(metrics.get("input_tokens", 0)),
            "output_tokens": int(metrics.get("output_tokens", 0)),
            "cost_usd": float(metrics.get("total_cost_usd", 0.0)),
        }

    async def stream_execute(self, prompt: str) -> AsyncIterator[str]:
        self.last_result_metrics = None
        if not self._connected or not self.client:
            await self.connect()
        await self.client.query(prompt, session_id=self.session_id)
        async for message in self.client.receive_messages():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield block.text
            elif isinstance(message, ResultMessage):
                self.last_result_metrics = SDKMetrics.from_result_message(message)
                break

    async def disconnect(self) -> None:
        if self.client:
            await self.client.disconnect()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class SkillClient:
    """Facade that preserves the existing runtime client interface."""

    def __init__(
        self,
        session_id: str = "orchestrator",
        allowed_tools: Optional[list[str]] = None,
        disallowed_tools: Optional[list[str]] = None,
        cwd: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        model: str = "sonnet",
    ):
        backend = _resolve_runtime_backend()
        client_cls = _OpenAISessionClient if backend == "openai" else _ClaudeSessionClient
        self._delegate = client_cls(
            session_id=session_id,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            cwd=cwd,
            log_callback=log_callback,
            model=model,
        )

    @property
    def last_result_metrics(self) -> Optional[SDKMetrics]:
        return self._delegate.last_result_metrics

    @last_result_metrics.setter
    def last_result_metrics(self, value: Optional[SDKMetrics]) -> None:
        self._delegate.last_result_metrics = value

    async def connect(self, initial_prompt: Optional[str] = None) -> None:
        await self._delegate.connect(initial_prompt)

    async def execute(self, prompt: str) -> str:
        return await self._delegate.execute(prompt)

    async def execute_with_metrics(self, prompt: str) -> tuple[str, dict[str, Any]]:
        return await self._delegate.execute_with_metrics(prompt)

    async def stream_execute(self, prompt: str) -> AsyncIterator[str]:
        async for chunk in self._delegate.stream_execute(prompt):
            yield chunk

    async def disconnect(self) -> None:
        await self._delegate.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._delegate.is_connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
