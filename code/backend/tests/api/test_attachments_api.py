import os

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.api.routes import attachments as attachments_route
from app.core.config import reset_settings_cache
from app.core.container import reset_container_cache
from app.main import create_app
from app.services.attachments import MAX_ATTACHMENT_REQUEST_SIZE_BYTES, MAX_ATTACHMENT_SIZE_BYTES


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


def _issue_session(client: TestClient) -> dict:
    response = client.post("/api/v1/attachments/sessions")
    assert response.status_code == 201
    payload = response.json()
    assert payload["session_id"]
    assert payload["session_token"]
    return payload


def test_session_issue_upload_list_and_download(client: TestClient) -> None:
    session = _issue_session(client)

    upload_response = client.post(
        "/api/v1/attachments",
        data={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
        files={"file": ("brief.txt", b"creative brief v1", "text/plain")},
    )
    assert upload_response.status_code == 201
    uploaded = upload_response.json()
    assert uploaded["id"]
    assert uploaded["session_id"] == session["session_id"]
    assert uploaded["filename"] == "brief.txt"
    assert uploaded["size_bytes"] == len(b"creative brief v1")

    list_response = client.get(
        "/api/v1/attachments",
        params={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]

    download_response = client.get(
        f"/api/v1/attachments/{uploaded['id']}/download",
        params={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
    )
    assert download_response.status_code == 200
    assert download_response.content == b"creative brief v1"


def test_invalid_or_missing_session_credentials_are_rejected(client: TestClient) -> None:
    valid_session = _issue_session(client)
    invalid_session = _issue_session(client)

    missing_credentials = client.post(
        "/api/v1/attachments",
        files={"file": ("brief.txt", b"creative brief v1", "text/plain")},
    )
    assert missing_credentials.status_code == 422

    invalid_upload = client.post(
        "/api/v1/attachments",
        data={
            "session_id": valid_session["session_id"],
            "session_token": invalid_session["session_token"],
        },
        files={"file": ("brief.txt", b"creative brief v1", "text/plain")},
    )
    assert invalid_upload.status_code == 401
    assert invalid_upload.json()["detail"] == "Invalid attachment session credentials"

    invalid_list = client.get(
        "/api/v1/attachments",
        params={
            "session_id": valid_session["session_id"],
            "session_token": invalid_session["session_token"],
        },
    )
    assert invalid_list.status_code == 401
    assert invalid_list.json()["detail"] == "Invalid attachment session credentials"


def test_upload_accepts_file_at_exact_25mb_boundary(client: TestClient) -> None:
    session = _issue_session(client)
    exact_payload = b"x" * MAX_ATTACHMENT_SIZE_BYTES

    response = client.post(
        "/api/v1/attachments",
        data={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
        files={"file": ("exact.bin", exact_payload, "application/octet-stream")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["size_bytes"] == MAX_ATTACHMENT_SIZE_BYTES


def test_upload_rejects_files_over_25mb(client: TestClient) -> None:
    session = _issue_session(client)
    oversized_payload = b"x" * (MAX_ATTACHMENT_SIZE_BYTES + 1)

    response = client.post(
        "/api/v1/attachments",
        data={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
        files={"file": ("huge.bin", oversized_payload, "application/octet-stream")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Attachment exceeds 25 MB size limit"


def test_upload_rejects_request_bytes_before_form_parsing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def _form_should_not_run(self, **kwargs):  # noqa: ANN001
        raise AssertionError("request.form should not run when request bytes exceed upload limit")

    monkeypatch.setattr(Request, "form", _form_should_not_run)

    response = client.post(
        "/api/v1/attachments",
        content=b"--boundary--\r\n",
        headers={
            "content-type": "multipart/form-data; boundary=boundary",
            "content-length": str(MAX_ATTACHMENT_REQUEST_SIZE_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Attachment exceeds 25 MB size limit"


def test_upload_reads_file_in_chunks(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    session = _issue_session(client)
    read_sizes: list[int | None] = []

    original_read = UploadFile.read

    async def _tracked_read(self, size: int = -1):  # noqa: ANN001
        read_sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(attachments_route, "UPLOAD_READ_CHUNK_SIZE", 8)
    monkeypatch.setattr(UploadFile, "read", _tracked_read)

    response = client.post(
        "/api/v1/attachments",
        data={
            "session_id": session["session_id"],
            "session_token": session["session_token"],
        },
        files={"file": ("chunked.txt", b"1234567890abcdef", "text/plain")},
    )
    assert response.status_code == 201
    assert read_sizes
    assert all(size not in (-1, None) for size in read_sizes)
