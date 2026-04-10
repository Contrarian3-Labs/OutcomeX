import os

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.datastructures import UploadFile

from app.api.routes import attachments as attachments_route
from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "attachments.db"
    os.environ["OUTCOMEX_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    os.environ["OUTCOMEX_AUTO_CREATE_TABLES"] = "true"
    reset_settings_cache()
    reset_container_cache()

    with TestClient(create_app()) as test_client:
        yield test_client

    reset_settings_cache()
    reset_container_cache()


def test_upload_list_and_download_attachment(client: TestClient) -> None:
    upload_response = client.post(
        "/api/v1/attachments",
        data={"session_kind": "chat", "session_id": "session-1"},
        files={"file": ("brief.txt", b"creative brief v1", "text/plain")},
    )
    assert upload_response.status_code == 201
    uploaded = upload_response.json()
    assert uploaded["id"]
    assert uploaded["session_kind"] == "chat"
    assert uploaded["session_id"] == "session-1"
    assert uploaded["filename"] == "brief.txt"
    assert uploaded["size_bytes"] == len(b"creative brief v1")
    assert uploaded["content_type"] == "text/plain"

    list_response = client.get("/api/v1/attachments", params={"session_kind": "chat", "session_id": "session-1"})
    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]
    assert listed[0]["filename"] == "brief.txt"
    assert listed[0]["size_bytes"] == len(b"creative brief v1")

    download_response = client.get(
        f"/api/v1/attachments/{uploaded['id']}/download",
        params={"session_kind": "chat", "session_id": "session-1"},
    )
    assert download_response.status_code == 200
    assert download_response.content == b"creative brief v1"
    assert "attachment; filename=\"brief.txt\"" == download_response.headers["content-disposition"]
    assert download_response.headers["content-type"].startswith("text/plain")


def test_attachment_isolation_prevents_cross_session_access(client: TestClient) -> None:
    upload_response = client.post(
        "/api/v1/attachments",
        data={"session_kind": "chat", "session_id": "session-a"},
        files={"file": ("owner.txt", b"owner-only", "text/plain")},
    )
    assert upload_response.status_code == 201
    attachment_id = upload_response.json()["id"]

    other_session_list = client.get("/api/v1/attachments", params={"session_kind": "chat", "session_id": "session-b"})
    assert other_session_list.status_code == 200
    assert other_session_list.json() == []

    forbidden_download = client.get(
        f"/api/v1/attachments/{attachment_id}/download",
        params={"session_kind": "chat", "session_id": "session-b"},
    )
    assert forbidden_download.status_code == 404
    assert forbidden_download.json()["detail"] == "Attachment not found"


def test_upload_rejects_files_over_25mb(client: TestClient) -> None:
    oversized_payload = b"x" * ((25 * 1024 * 1024) + 1)

    response = client.post(
        "/api/v1/attachments",
        data={"session_kind": "chat", "session_id": "session-1"},
        files={"file": ("huge.bin", oversized_payload, "application/octet-stream")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Attachment exceeds 25 MB size limit"


def test_upload_rejects_large_content_length_before_form_parsing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def _form_should_not_run(self, **kwargs):  # noqa: ANN001
        raise AssertionError("request.form should not run when content-length is too large")

    monkeypatch.setattr(Request, "form", _form_should_not_run)

    response = client.post(
        "/api/v1/attachments",
        content=b"x" * (attachments_route.MAX_ATTACHMENT_SIZE_BYTES + 1),
        headers={
            "content-type": "application/octet-stream",
            "content-length": str(attachments_route.MAX_ATTACHMENT_SIZE_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Attachment exceeds 25 MB size limit"


def test_upload_reads_in_chunks_instead_of_full_buffer(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    read_sizes: list[int | None] = []
    original_read = UploadFile.read

    async def _tracked_read(self, size: int = -1):  # noqa: ANN001
        read_sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(attachments_route, "UPLOAD_READ_CHUNK_SIZE", 8)
    monkeypatch.setattr(UploadFile, "read", _tracked_read)

    response = client.post(
        "/api/v1/attachments",
        data={"session_kind": "chat", "session_id": "session-1"},
        files={"file": ("chunked.txt", b"1234567890abcdef", "text/plain")},
    )
    assert response.status_code == 201
    assert read_sizes
    assert all(size not in (-1, None) for size in read_sizes)
