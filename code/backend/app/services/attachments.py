import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.domain.models import Attachment, AttachmentSession


MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_ATTACHMENT_REQUEST_SIZE_BYTES = MAX_ATTACHMENT_SIZE_BYTES + MAX_ATTACHMENT_MULTIPART_OVERHEAD_BYTES
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DEFAULT_FILENAME = "attachment.bin"


class AttachmentTooLargeError(ValueError):
    """Raised when an uploaded attachment is larger than the configured limit."""


def _hash_session_token(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def create_attachment_session(*, db: Session) -> tuple[AttachmentSession, str]:
    session_token = secrets.token_urlsafe(32)
    attachment_session = AttachmentSession(token_hash=_hash_session_token(session_token))
    db.add(attachment_session)
    db.commit()
    db.refresh(attachment_session)
    return attachment_session, session_token


def resolve_attachment_session(*, db: Session, session_id: str, session_token: str) -> AttachmentSession | None:
    attachment_session = db.get(AttachmentSession, session_id)
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

    attachment = Attachment(
        attachment_session_id=attachment_session_id,
        filename=(filename or DEFAULT_FILENAME).strip() or DEFAULT_FILENAME,
        content_type=(content_type or DEFAULT_CONTENT_TYPE).strip() or DEFAULT_CONTENT_TYPE,
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
