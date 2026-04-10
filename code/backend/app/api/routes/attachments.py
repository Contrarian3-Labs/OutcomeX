from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.deps import get_db
from app.schemas.attachment import AttachmentResponse
from app.services.attachments import (
    MAX_ATTACHMENT_SIZE_BYTES,
    AttachmentTooLargeError,
    create_attachment,
    get_attachment_for_session,
    list_attachments,
)

router = APIRouter()
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
MAX_SESSION_KIND_LENGTH = 32
MAX_SESSION_ID_LENGTH = 128
MAX_MULTIPART_FIELDS = 20
MAX_MULTIPART_FILES = 4


def _normalize_session_value(raw_value: object, *, field_name: str, max_length: int) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{field_name} is required")
    if len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field_name} exceeds {max_length} characters",
        )
    return normalized


def _early_reject_by_content_length(request: Request) -> None:
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is None:
        return
    try:
        content_length = int(raw_content_length)
    except ValueError:
        return
    if content_length > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Attachment exceeds 25 MB size limit",
        )


async def _read_payload_with_limit(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > MAX_ATTACHMENT_SIZE_BYTES:
            raise AttachmentTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(request: Request, db: Session = Depends(get_db)) -> AttachmentResponse:
    _early_reject_by_content_length(request)
    try:
        form = await request.form(
            max_part_size=MAX_ATTACHMENT_SIZE_BYTES,
            max_fields=MAX_MULTIPART_FIELDS,
            max_files=MAX_MULTIPART_FILES,
        )
    except MultiPartException as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Attachment exceeds 25 MB size limit",
        ) from exc

    session_kind = _normalize_session_value(
        form.get("session_kind"),
        field_name="session_kind",
        max_length=MAX_SESSION_KIND_LENGTH,
    )
    session_id = _normalize_session_value(
        form.get("session_id"),
        field_name="session_id",
        max_length=MAX_SESSION_ID_LENGTH,
    )

    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="file is required")

    try:
        payload = await _read_payload_with_limit(file)
    finally:
        await file.close()

    try:
        attachment = create_attachment(
            db=db,
            session_kind=session_kind,
            session_id=session_id,
            filename=file.filename,
            content_type=file.content_type,
            payload=payload,
        )
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Attachment exceeds 25 MB size limit",
        ) from exc
    return AttachmentResponse.model_validate(attachment)


@router.get("", response_model=list[AttachmentResponse])
def list_uploaded_attachments(
    session_kind: str = Query(min_length=1, max_length=MAX_SESSION_KIND_LENGTH),
    session_id: str = Query(min_length=1, max_length=MAX_SESSION_ID_LENGTH),
    db: Session = Depends(get_db),
) -> list[AttachmentResponse]:
    attachments = list_attachments(
        db=db,
        session_kind=session_kind.strip(),
        session_id=session_id.strip(),
    )
    return [AttachmentResponse.model_validate(item) for item in attachments]


@router.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    session_kind: str = Query(min_length=1, max_length=MAX_SESSION_KIND_LENGTH),
    session_id: str = Query(min_length=1, max_length=MAX_SESSION_ID_LENGTH),
    db: Session = Depends(get_db),
) -> Response:
    attachment = get_attachment_for_session(
        db=db,
        attachment_id=attachment_id,
        session_kind=session_kind.strip(),
        session_id=session_id.strip(),
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.filename}"',
        },
    )
