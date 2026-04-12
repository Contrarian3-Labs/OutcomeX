from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload=None, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, *, result_url: str, output_bytes: bytes) -> None:
        self.result_url = result_url
        self.output_bytes = output_bytes
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def post(self, url: str, headers: dict, json: dict, timeout: int):
        self.posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse({"output": {"task_id": "task-123"}})

    def get(self, url: str, headers=None, timeout: int = 60):
        self.gets.append(url)
        if url == self.result_url:
            return _FakeResponse(content=self.output_bytes)
        return _FakeResponse(
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": self.result_url}],
                }
            }
        )


def _write_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_image_edit_script_smoke(tmp_path, monkeypatch) -> None:
    module = _load_module(
        "data/skill_seeds/image-edit-dashscope/scripts/image_edit.py",
        "image_edit_smoke",
    )
    input_path = _write_bytes(tmp_path / "source.png", b"\x89PNG\r\n\x1a\nfake")
    output_path = tmp_path / "edited.png"
    fake_requests = _FakeRequests(result_url="https://example.com/edited.png", output_bytes=b"edited-image")

    monkeypatch.setattr(module, "_require_requests", lambda: fake_requests)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "image_edit.py",
            "Remove the background clutter",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0
    assert output_path.read_bytes() == b"edited-image"
    payload = fake_requests.posts[0]["json"]
    assert payload["model"] == "wan2.7-image-pro"
    assert payload["input"]["messages"][0]["content"][0]["image"].startswith("data:image/png;base64,")


def test_image_to_video_script_smoke(tmp_path, monkeypatch) -> None:
    module = _load_module(
        "data/skill_seeds/wan-i2v-dashscope/scripts/image_to_video.py",
        "i2v_smoke",
    )
    input_path = _write_bytes(tmp_path / "frame.png", b"\x89PNG\r\n\x1a\nframe")
    output_path = tmp_path / "clip.mp4"
    fake_requests = _FakeRequests(result_url="https://example.com/clip.mp4", output_bytes=b"video-bytes")

    monkeypatch.setattr(module, "_require_requests", lambda: fake_requests)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "image_to_video.py",
            "Animate this image with a slow pan",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0
    assert output_path.read_bytes() == b"video-bytes"
    payload = fake_requests.posts[0]["json"]
    assert payload["model"] == "wan2.7-i2v"
    assert payload["input"]["media"][0]["type"] == "first_frame"
    assert payload["input"]["media"][0]["url"].startswith("data:image/png;base64,")


def test_reference_to_video_script_smoke(tmp_path, monkeypatch) -> None:
    module = _load_module(
        "data/skill_seeds/wan-r2v-dashscope/scripts/reference_to_video.py",
        "r2v_smoke",
    )
    input_path = _write_bytes(tmp_path / "reference.png", b"\x89PNG\r\n\x1a\nreference")
    output_path = tmp_path / "teaser.mp4"
    fake_requests = _FakeRequests(result_url="https://example.com/teaser.mp4", output_bytes=b"r2v-video")

    monkeypatch.setattr(module, "_require_requests", lambda: fake_requests)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reference_to_video.py",
            "Preserve character identity and create a teaser",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0
    assert output_path.read_bytes() == b"r2v-video"
    payload = fake_requests.posts[0]["json"]
    assert payload["model"] == "wan2.7-r2v"
    assert payload["input"]["media"][0]["type"] == "reference_image"
    assert payload["input"]["media"][0]["url"].startswith("data:image/png;base64,")


def test_text_to_video_script_smoke(tmp_path, monkeypatch) -> None:
    module = _load_module(
        "data/skill_seeds/wan-t2v-dashscope/scripts/text_to_video.py",
        "t2v_smoke",
    )
    output_path = tmp_path / "clip.mp4"
    fake_requests = _FakeRequests(result_url="https://example.com/generated.mp4", output_bytes=b"t2v-video")

    monkeypatch.setattr(module, "_require_requests", lambda: fake_requests)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "text_to_video.py",
            "Create a cinematic portrait video",
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0
    assert output_path.read_bytes() == b"t2v-video"
    payload = fake_requests.posts[0]["json"]
    assert payload["model"] == "wan2.2-t2v-plus"
    assert payload["input"]["prompt"] == "Create a cinematic portrait video"
    assert "media" not in payload["input"]


def test_video_edit_script_smoke(tmp_path, monkeypatch) -> None:
    module = _load_module(
        "data/skill_seeds/wan-videoedit-dashscope/scripts/video_edit.py",
        "video_edit_smoke",
    )
    input_path = _write_bytes(tmp_path / "source.mp4", b"\x00\x00\x00\x18ftypmp42fake")
    output_path = tmp_path / "edited.mp4"
    fake_requests = _FakeRequests(result_url="https://example.com/edited.mp4", output_bytes=b"edited-video")

    monkeypatch.setattr(module, "_require_requests", lambda: fake_requests)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "video_edit.py",
            "Turn this into a clay animation look",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0
    assert output_path.read_bytes() == b"edited-video"
    payload = fake_requests.posts[0]["json"]
    assert payload["model"] == "wan2.7-videoedit"
    assert payload["input"]["media"][0]["type"] == "video"
    assert payload["input"]["media"][0]["url"].startswith("data:video/mp4;base64,")
