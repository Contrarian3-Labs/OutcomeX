import os

import pytest
from fastapi.testclient import TestClient

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
        data={"user_id": "user-1"},
        files={"file": ("brief.txt", b"creative brief v1", "text/plain")},
    )
    assert upload_response.status_code == 201
    uploaded = upload_response.json()
    assert uploaded["id"]
    assert uploaded["user_id"] == "user-1"
    assert uploaded["filename"] == "brief.txt"
    assert uploaded["size_bytes"] == len(b"creative brief v1")
    assert uploaded["content_type"] == "text/plain"

    list_response = client.get("/api/v1/attachments", params={"user_id": "user-1"})
    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]
    assert listed[0]["filename"] == "brief.txt"
    assert listed[0]["size_bytes"] == len(b"creative brief v1")

    download_response = client.get(f"/api/v1/attachments/{uploaded['id']}/download")
    assert download_response.status_code == 200
    assert download_response.content == b"creative brief v1"
    assert "attachment; filename=\"brief.txt\"" == download_response.headers["content-disposition"]
    assert download_response.headers["content-type"].startswith("text/plain")


def test_upload_rejects_files_over_25mb(client: TestClient) -> None:
    oversized_payload = b"x" * ((25 * 1024 * 1024) + 1)

    response = client.post(
        "/api/v1/attachments",
        data={"user_id": "user-1"},
        files={"file": ("huge.bin", oversized_payload, "application/octet-stream")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Attachment exceeds 25 MB size limit"
