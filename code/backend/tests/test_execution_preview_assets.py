from pathlib import Path

from app.integrations import agentskillos_execution_service as service


def test_sanitize_visible_manifests_generates_html_preview_image(monkeypatch, tmp_path) -> None:
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<html><body>Hello</body></html>", encoding="utf-8")

    def fake_html_preview(*, run_dir: Path, relative_path: str) -> str | None:
        target = run_dir / ".outcomex-preview" / "workspace" / "index.html-preview.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        return target.relative_to(run_dir).as_posix()

    monkeypatch.setattr(service, "_ensure_html_preview_image", fake_html_preview)

    payload = {
        "run_dir": str(run_dir),
        "artifact_manifest": [{"path": "workspace/index.html", "type": "html", "role": "final"}],
        "preview_manifest": [{"path": "workspace/index.html", "type": "html", "role": "preview"}],
    }

    sanitized = service.sanitize_visible_manifests(payload)
    assert sanitized["preview_manifest"] == [
        {
            "path": ".outcomex-preview/workspace/index.html-preview.png",
            "type": "image",
            "kind": "html",
            "name": "workspace/index.html",
            "source_path": "workspace/index.html",
            "role": "preview",
        }
    ]


def test_sanitize_visible_manifests_generates_presentation_preview_image(monkeypatch, tmp_path) -> None:
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "deck.pptx").write_bytes(b"pptx")

    def fake_presentation_preview(*, run_dir: Path, relative_path: str) -> str | None:
        target = run_dir / ".outcomex-preview" / "workspace" / "deck.presentation-preview.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        return target.relative_to(run_dir).as_posix()

    monkeypatch.setattr(service, "_ensure_presentation_preview_image", fake_presentation_preview)

    payload = {
        "run_dir": str(run_dir),
        "artifact_manifest": [{"path": "workspace/deck.pptx", "type": "presentation", "role": "final"}],
        "preview_manifest": [],
    }

    sanitized = service.sanitize_visible_manifests(payload)
    assert sanitized["preview_manifest"] == [
        {
            "path": ".outcomex-preview/workspace/deck.presentation-preview.png",
            "type": "image",
            "kind": "presentation",
            "name": "workspace/deck.pptx",
            "source_path": "workspace/deck.pptx",
            "role": "preview",
        }
    ]
