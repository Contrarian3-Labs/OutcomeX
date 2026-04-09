from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EventReadResult:
    items: list[dict[str, Any]]
    next_cursor: int


def _to_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return value if isinstance(value, Path) else Path(value)


def _coerce_seq(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_log_file_item(*, kind: str, path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
        size = int(stat_result.st_size)
        updated_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
    except OSError:
        size = 0
        updated_at = None
    return {
        "kind": kind,
        "name": path.name,
        "path": str(path),
        "size": size,
        "updated_at": updated_at,
    }


def read_events_after_seq(events_path: str | Path | None, *, after_seq: int) -> EventReadResult:
    path = _to_path(events_path)
    items: list[dict[str, Any]] = []
    next_cursor = max(0, int(after_seq))
    if path is None or not path.exists() or not path.is_file():
        return EventReadResult(items=items, next_cursor=next_cursor)

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        seq = _coerce_seq(payload.get("seq"))
        if seq is None:
            continue
        next_cursor = max(next_cursor, seq)
        if seq <= after_seq:
            continue
        event = dict(payload)
        event["seq"] = seq
        items.append(event)
    return EventReadResult(items=items, next_cursor=next_cursor)


def list_log_files(
    *,
    run_dir: str | Path | None,
    stdout_path: str | Path | None,
    stderr_path: str | Path | None,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()

    run_dir_path = _to_path(run_dir)
    if run_dir_path is not None:
        logs_dir = run_dir_path / "logs"
        if logs_dir.exists() and logs_dir.is_dir():
            for candidate in sorted(logs_dir.iterdir(), key=lambda item: item.name):
                if not candidate.is_file():
                    continue
                files.append(_build_log_file_item(kind="raw_file", path=candidate))
                seen_paths.add(candidate.resolve())

    for kind, candidate in (("stdout", _to_path(stdout_path)), ("stderr", _to_path(stderr_path))):
        if candidate is None or not candidate.exists() or not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved in seen_paths:
            continue
        files.append(_build_log_file_item(kind=kind, path=candidate))
        seen_paths.add(resolved)

    return files
