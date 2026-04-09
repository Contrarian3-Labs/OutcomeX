from __future__ import annotations

from pathlib import Path

from app.execution.observability import list_log_files, read_events_after_seq


def test_read_events_after_seq_returns_only_newer_events_and_next_cursor(tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        "\n".join(
            [
                '{"seq":1,"event":"run_started"}',
                '{"seq":2,"event":"skills_discovered"}',
                '{"seq":5,"event":"plan_selected"}',
            ]
        ),
        encoding="utf-8",
    )

    result = read_events_after_seq(events, after_seq=1)

    assert [item["seq"] for item in result.items] == [2, 5]
    assert result.next_cursor == 5


def test_read_events_after_seq_skips_malformed_lines_safely(tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        "\n".join(
            [
                '{"seq":1,"event":"run_started"}',
                '{"seq":',
                "[]",
                '{"seq":"bad","event":"broken"}',
                '{"seq":3,"event":"plan_selected"}',
            ]
        ),
        encoding="utf-8",
    )

    result = read_events_after_seq(events, after_seq=1)

    assert [item["seq"] for item in result.items] == [3]
    assert result.next_cursor == 3


def test_read_events_after_seq_handles_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing-events.ndjson"

    result = read_events_after_seq(missing, after_seq=4)

    assert result.items == []
    assert result.next_cursor == 4


def test_list_log_files_prioritizes_raw_logs_then_stdout_stderr(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "z-planner.log").write_text("planner", encoding="utf-8")
    (logs_dir / "a-discovery.log").write_text("discovery", encoding="utf-8")

    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    stdout_path.write_text("stdout line", encoding="utf-8")
    stderr_path.write_text("stderr line", encoding="utf-8")

    files = list_log_files(run_dir=run_dir, stdout_path=stdout_path, stderr_path=stderr_path)

    assert [item["kind"] for item in files] == ["raw_file", "raw_file", "stdout", "stderr"]
    assert [item["name"] for item in files] == ["a-discovery.log", "z-planner.log", "stdout.log", "stderr.log"]


def test_list_log_files_returns_sane_metadata_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True)
    raw_file = logs_dir / "planner.log"
    raw_file.write_text("line1\nline2\n", encoding="utf-8")

    files = list_log_files(run_dir=run_dir, stdout_path=None, stderr_path=None)

    assert len(files) == 1
    item = files[0]
    assert set(item.keys()) == {"kind", "name", "path", "size", "updated_at"}
    assert item["kind"] == "raw_file"
    assert item["name"] == "planner.log"
    assert item["path"] == str(raw_file)
    assert isinstance(item["size"], int)
    assert item["size"] > 0
    assert isinstance(item["updated_at"], str)
    assert item["updated_at"].endswith("Z")
