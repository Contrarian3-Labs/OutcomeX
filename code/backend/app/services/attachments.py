import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, load_only

from app.domain.models import Attachment, AttachmentSession, utc_now


MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_ATTACHMENT_REQUEST_SIZE_BYTES = MAX_ATTACHMENT_SIZE_BYTES + MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES
MAX_ATTACHMENT_FILENAME_LENGTH = 512
MAX_ATTACHMENT_CONTENT_TYPE_LENGTH = 128
MAX_ATTACHMENTS_PER_SESSION = 32
MAX_TOTAL_BYTES_PER_SESSION = 100 * 1024 * 1024
ATTACHMENT_SESSION_TTL = timedelta(hours=24)
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DEFAULT_FILENAME = "attachment.bin"


class AttachmentTooLargeError(ValueError):
    """Raised when an uploaded attachment is larger than the configured limit."""


class AttachmentSessionQuotaExceededError(ValueError):
    """Raised when a session exceeds attachment count or total bytes quota."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AttachmentMetadataInvalidError(ValueError):
    """Raised when filename or content-type metadata is invalid."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _hash_session_token(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return True
    now_value = now or utc_now()
    if expires_at.tzinfo is None:
        return expires_at <= now_value.replace(tzinfo=None)
    return expires_at <= now_value.astimezone(expires_at.tzinfo)


def _sanitize_metadata_value(value: str) -> str:
    return "".join(ch for ch in value if 32 <= ord(ch) < 127).strip()


def _normalize_filename(value: str | None) -> str:
    normalized = _sanitize_metadata_value(value or DEFAULT_FILENAME)
    if not normalized:
        normalized = DEFAULT_FILENAME
    if len(normalized) > MAX_ATTACHMENT_FILENAME_LENGTH:
        raise AttachmentMetadataInvalidError(
            f"filename exceeds {MAX_ATTACHMENT_FILENAME_LENGTH} characters"
        )
    return normalized


def _normalize_content_type(value: str | None) -> str:
    normalized = _sanitize_metadata_value(value or DEFAULT_CONTENT_TYPE)
    if not normalized:
        normalized = DEFAULT_CONTENT_TYPE
    if len(normalized) > MAX_ATTACHMENT_CONTENT_TYPE_LENGTH:
        raise AttachmentMetadataInvalidError(
            f"content_type exceeds {MAX_ATTACHMENT_CONTENT_TYPE_LENGTH} characters"
        )
    return normalized


def cleanup_expired_attachment_sessions(*, db: Session) -> int:
    now_utc = datetime.now(timezone.utc)
    session_rows = db.execute(
        select(AttachmentSession.id, AttachmentSession.expires_at)
    ).all()
    expired_session_ids = [
        session_id
        for session_id, expires_at in session_rows
        if _is_expired(expires_at, now=now_utc)
    ]
    if not expired_session_ids:
        return 0

    db.execute(delete(Attachment).where(Attachment.attachment_session_id.in_(expired_session_ids)))
    db.execute(delete(AttachmentSession).where(AttachmentSession.id.in_(expired_session_ids)))
    db.commit()
    return len(expired_session_ids)


def create_attachment_session(*, db: Session) -> tuple[AttachmentSession, str]:
    cleanup_expired_attachment_sessions(db=db)
    session_token = secrets.token_urlsafe(32)
    attachment_session = AttachmentSession(
        token_hash=_hash_session_token(session_token),
        expires_at=utc_now() + ATTACHMENT_SESSION_TTL,
    )
    db.add(attachment_session)
    db.commit()
    db.refresh(attachment_session)
    return attachment_session, session_token


def resolve_attachment_session(*, db: Session, session_id: str, session_token: str) -> AttachmentSession | None:
    cleanup_expired_attachment_sessions(db=db)
    attachment_session = db.get(AttachmentSession, session_id)
    if attachment_session is None:
        return None
    if _is_expired(attachment_session.expires_at):
        db.execute(delete(Attachment).where(Attachment.attachment_session_id == attachment_session.id))
        db.delete(attachment_session)
        db.commit()
        return None
    supplied_hash = _hash_session_token(session_token)
    if not secrets.compare_digest(attachment_session.token_hash, supplied_hash):
        return None
    return attachment_session


def create_attachment(
    *,
    db: Session,
    attachment_session_id: str,
    filename: str | None,
    content_type: str | None,
    payload: bytes,
) -> Attachment:
    if len(payload) > MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentTooLargeError

    existing_count, existing_total_bytes = db.execute(
        select(
            func.count(Attachment.id),
            func.coalesce(func.sum(Attachment.size_bytes), 0),
        ).where(Attachment.attachment_session_id == attachment_session_id)
    ).one()
    if int(existing_count or 0) >= MAX_ATTACHMENTS_PER_SESSION:
        raise AttachmentSessionQuotaExceededError("Attachment session file-count quota exceeded")
    if int(existing_total_bytes or 0) + len(payload) > MAX_TOTAL_BYTES_PER_SESSION:
        raise AttachmentSessionQuotaExceededError("Attachment session total-bytes quota exceeded")

    attachment = Attachment(
        attachment_session_id=attachment_session_id,
        filename=_normalize_filename(filename),
        content_type=_normalize_content_type(content_type),
        size_bytes=len(payload),
        content=payload,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def list_attachments(*, db: Session, attachment_session_id: str) -> list[Attachment]:
    query = (
        select(Attachment)
        .options(
            load_only(
                Attachment.id,
                Attachment.attachment_session_id,
                Attachment.filename,
                Attachment.content_type,
                Attachment.size_bytes,
                Attachment.created_at,
            )
        )
        .where(Attachment.attachment_session_id == attachment_session_id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
    )
    return list(db.execute(query).scalars().all())


def get_attachment_for_session(
    *, db: Session, attachment_id: str, attachment_session_id: str
) -> Attachment | None:
    return db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.attachment_session_id == attachment_session_id,
        )
    )
