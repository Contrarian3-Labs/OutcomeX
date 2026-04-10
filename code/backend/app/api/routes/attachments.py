from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.deps import get_db
from app.schemas.attachment import AttachmentResponse, AttachmentSessionCreateResponse
from app.services.attachments import (
    MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES,
    MAX_ATTACHMENT_REQUEST_SIZE_BYTES,
    MAX_ATTACHMENT_SIZE_BYTES,
    AttachmentMetadataInvalidError,
    AttachmentSessionQuotaExceededError,
    AttachmentTooLargeError,
    cleanup_expired_attachment_sessions,
    create_attachment,
    create_attachment_session,
    get_attachment_for_session,
    list_attachments,
    resolve_attachment_session,
)

router = APIRouter()
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
MAX_SESSION_ID_LENGTH = 64
MAX_SESSION_TOKEN_LENGTH = 256
MAX_MULTIPART_FIELDS = 20
MAX_MULTIPART_FILES = 4
INVALID_SESSION_DETAIL = "Invalid attachment session credentials"
PRIVATE_CACHE_CONTROL = "no-store, private"
SESSION_TOKEN_VARY = "X-Attachment-Session-Token"


def _normalize_required_value(raw_value: object, *, field_name: str, max_length: int) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{field_name} is required")
    if len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field_name} exceeds {max_length} characters",
        )
    return normalized


def _session_token_header(
    x_attachment_session_token: str = Header(
        ...,
        alias="X-Attachment-Session-Token",
        description="Server-issued attachment session secret token.",
    ),
) -> str:
    return _normalize_required_value(
        x_attachment_session_token,
        field_name="X-Attachment-Session-Token",
        max_length=MAX_SESSION_TOKEN_LENGTH,
    )


def _apply_private_session_headers(response: Response) -> None:
    response.headers["Cache-Control"] = PRIVATE_CACHE_CONTROL
    existing_vary = response.headers.get("Vary")
    if not existing_vary:
        response.headers["Vary"] = SESSION_TOKEN_VARY
        return
    existing_tokens = {token.strip() for token in existing_vary.split(",") if token.strip()}
    if SESSION_TOKEN_VARY in existing_tokens:
        return
    response.headers["Vary"] = f"{existing_vary}, {SESSION_TOKEN_VARY}"


def _private_session_headers() -> dict[str, str]:
    return {
        "Cache-Control": PRIVATE_CACHE_CONTROL,
        "Vary": SESSION_TOKEN_VARY,
    }


def _build_content_disposition(filename: str) -> str:
    sanitized = filename.replace("\r", "").replace("\n", "").strip()
    if not sanitized:
        sanitized = "attachment.bin"

    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\"} else "_"
        for ch in sanitized
    ).strip() or "attachment.bin"
    encoded = quote(sanitized, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _early_reject_by_content_length(request: Request) -> None:
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is None:
        return
    try:
        content_length = int(raw_content_length)
    except ValueError:
        return
    if content_length > MAX_ATTACHMENT_REQUEST_SIZE_BYTES:
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


def _build_attachment_response(attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        session_id=attachment.attachment_session_id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        created_at=attachment.created_at,
    )


def _resolve_session_or_401(*, db: Session, session_id: str, session_token: str):
    attachment_session = resolve_attachment_session(
        db=db,
        session_id=session_id,
        session_token=session_token,
    )
    if attachment_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_SESSION_DETAIL,
            headers=_private_session_headers(),
        )
    return attachment_session


@router.post("/sessions", response_model=AttachmentSessionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_upload_session(db: Session = Depends(get_db)) -> AttachmentSessionCreateResponse:
    cleanup_expired_attachment_sessions(db=db)
    attachment_session, session_token = create_attachment_session(db=db)
    return AttachmentSessionCreateResponse(
        session_id=attachment_session.id,
        session_token=session_token,
        created_at=attachment_session.created_at,
        expires_at=attachment_session.expires_at,
    )


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    session_token: str = Depends(_session_token_header),
    db: Session = Depends(get_db),
) -> AttachmentResponse:
    cleanup_expired_attachment_sessions(db=db)
    _early_reject_by_content_length(request)
    try:
        form = await request.form(
            max_part_size=MAX_ATTACHMENT_SIZE_BYTES + MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES,
            max_fields=MAX_MULTIPART_FIELDS,
            max_files=MAX_MULTIPART_FILES,
        )
    except MultiPartException as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Attachment exceeds 25 MB size limit",
        ) from exc

    session_id = _normalize_required_value(
        form.get("session_id"),
        field_name="session_id",
        max_length=MAX_SESSION_ID_LENGTH,
    )
    attachment_session = _resolve_session_or_401(db=db, session_id=session_id, session_token=session_token)

    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="file is required")

    try:
        try:
            payload = await _read_payload_with_limit(file)
        finally:
            await file.close()

        attachment = create_attachment(
            db=db,
            attachment_session_id=attachment_session.id,
            filename=file.filename,
            content_type=file.content_type,
            payload=payload,
        )
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Attachment exceeds 25 MB size limit",
        ) from exc
    except AttachmentSessionQuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except AttachmentMetadataInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc
    return _build_attachment_response(attachment)


@router.get("", response_model=list[AttachmentResponse])
def list_uploaded_attachments(
    response: Response,
    session_id: str = Query(min_length=1, max_length=MAX_SESSION_ID_LENGTH),
    session_token: str = Depends(_session_token_header),
    db: Session = Depends(get_db),
) -> list[AttachmentResponse]:
    cleanup_expired_attachment_sessions(db=db)
    attachment_session = _resolve_session_or_401(
        db=db,
        session_id=session_id.strip(),
        session_token=session_token.strip(),
    )
    attachments = list_attachments(
        db=db,
        attachment_session_id=attachment_session.id,
    )
    _apply_private_session_headers(response)
    return [_build_attachment_response(item) for item in attachments]


@router.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    session_id: str = Query(min_length=1, max_length=MAX_SESSION_ID_LENGTH),
    session_token: str = Depends(_session_token_header),
    db: Session = Depends(get_db),
) -> Response:
    cleanup_expired_attachment_sessions(db=db)
    attachment_session = _resolve_session_or_401(
        db=db,
        session_id=session_id.strip(),
        session_token=session_token.strip(),
    )
    attachment = get_attachment_for_session(
        db=db,
        attachment_id=attachment_id,
        attachment_session_id=attachment_session.id,
    )
    if attachment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found",
            headers=_private_session_headers(),
        )

    response = Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": _build_content_disposition(attachment.filename),
        },
    )
    _apply_private_session_headers(response)
    return response
