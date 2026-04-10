import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update
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
CLEANUP_BATCH_SIZE = 100
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


def _utc_cutoff() -> object:
    now = utc_now()
    return now.replace(tzinfo=None)


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
    total_deleted = 0
    cutoff = _utc_cutoff()
    while True:
        expired_session_ids = list(
            db.execute(
                select(AttachmentSession.id)
                .where(AttachmentSession.expires_at <= cutoff)
                .limit(CLEANUP_BATCH_SIZE)
            ).scalars().all()
        )
        if not expired_session_ids:
            break

        db.execute(delete(Attachment).where(Attachment.attachment_session_id.in_(expired_session_ids)))
        db.execute(delete(AttachmentSession).where(AttachmentSession.id.in_(expired_session_ids)))
        db.commit()
        total_deleted += len(expired_session_ids)
        if len(expired_session_ids) < CLEANUP_BATCH_SIZE:
            break
    return total_deleted


def create_attachment_session(*, db: Session) -> tuple[AttachmentSession, str]:
    cleanup_expired_attachment_sessions(db=db)
    session_token = secrets.token_urlsafe(32)
    attachment_session = AttachmentSession(
        token_hash=_hash_session_token(session_token),
        expires_at=utc_now() + ATTACHMENT_SESSION_TTL,
        attachment_count=0,
        total_size_bytes=0,
    )
    db.add(attachment_session)
    db.commit()
    db.refresh(attachment_session)
    return attachment_session, session_token


def resolve_attachment_session(*, db: Session, session_id: str, session_token: str) -> AttachmentSession | None:
    cleanup_expired_attachment_sessions(db=db)
    attachment_session = db.scalar(
        select(AttachmentSession).where(
            AttachmentSession.id == session_id,
            AttachmentSession.expires_at > _utc_cutoff(),
        )
    )
    if attachment_session is None:
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

    normalized_filename = _normalize_filename(filename)
    normalized_content_type = _normalize_content_type(content_type)
    payload_size = len(payload)

    quota_update = (
        update(AttachmentSession)
        .where(
            AttachmentSession.id == attachment_session_id,
            AttachmentSession.expires_at > _utc_cutoff(),
            AttachmentSession.attachment_count < MAX_ATTACHMENTS_PER_SESSION,
            (AttachmentSession.total_size_bytes + payload_size) <= MAX_TOTAL_BYTES_PER_SESSION,
        )
        .values(
            attachment_count=AttachmentSession.attachment_count + 1,
            total_size_bytes=AttachmentSession.total_size_bytes + payload_size,
        )
    )
    quota_claim = db.execute(quota_update)
    if quota_claim.rowcount != 1:
        quota_state = db.execute(
            select(AttachmentSession.attachment_count, AttachmentSession.total_size_bytes).where(
                AttachmentSession.id == attachment_session_id,
                AttachmentSession.expires_at > _utc_cutoff(),
            )
        ).one_or_none()
        if quota_state is None:
            raise AttachmentSessionQuotaExceededError("Attachment session is expired or unavailable")
        if int(quota_state.attachment_count or 0) >= MAX_ATTACHMENTS_PER_SESSION:
            raise AttachmentSessionQuotaExceededError("Attachment session file-count quota exceeded")
        raise AttachmentSessionQuotaExceededError("Attachment session total-bytes quota exceeded")

    attachment = Attachment(
        attachment_session_id=attachment_session_id,
        filename=normalized_filename,
        content_type=normalized_content_type,
        size_bytes=payload_size,
        content=payload,
    )
    db.add(attachment)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
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
