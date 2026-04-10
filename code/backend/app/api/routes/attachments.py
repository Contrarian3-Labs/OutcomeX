from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.attachment import AttachmentResponse
from app.services.attachments import (
    MAX_ATTACHMENT_SIZE_BYTES,
    AttachmentTooLargeError,
    create_attachment,
    get_attachment_for_user,
    list_attachments,
)

router = APIRouter()
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


def _request_user_id(
    x_outcomex_user_id: str | None = Header(default=None, alias="X-OutcomeX-User-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    caller = (x_outcomex_user_id or x_user_id or "").strip()
    if caller:
        return caller
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing caller user id")


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
async def upload_attachment(
    file: UploadFile = File(...),
    user_id: str = Depends(_request_user_id),
    db: Session = Depends(get_db),
) -> AttachmentResponse:
    try:
        try:
            payload = await _read_payload_with_limit(file)
        finally:
            await file.close()

        attachment = create_attachment(
            db=db,
            user_id=user_id,
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
    user_id: str = Depends(_request_user_id),
    db: Session = Depends(get_db),
) -> list[AttachmentResponse]:
    attachments = list_attachments(db=db, user_id=user_id)
    return [AttachmentResponse.model_validate(item) for item in attachments]


@router.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    user_id: str = Depends(_request_user_id),
    db: Session = Depends(get_db),
) -> Response:
    attachment = get_attachment_for_user(db=db, attachment_id=attachment_id, user_id=user_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.filename}"',
        },
    )
