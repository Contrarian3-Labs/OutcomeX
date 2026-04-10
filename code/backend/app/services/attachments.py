from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.domain.models import Attachment


MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DEFAULT_FILENAME = "attachment.bin"


class AttachmentTooLargeError(ValueError):
    """Raised when an uploaded attachment is larger than the configured limit."""


def create_attachment(
    *,
    db: Session,
    user_id: str,
    filename: str | None,
    content_type: str | None,
    payload: bytes,
) -> Attachment:
    if len(payload) > MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentTooLargeError

    attachment = Attachment(
        user_id=user_id,
        filename=(filename or DEFAULT_FILENAME).strip() or DEFAULT_FILENAME,
        content_type=(content_type or DEFAULT_CONTENT_TYPE).strip() or DEFAULT_CONTENT_TYPE,
        size_bytes=len(payload),
        content=payload,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def list_attachments(*, db: Session, user_id: str) -> list[Attachment]:
    query = (
        select(Attachment)
        .options(
            load_only(
                Attachment.id,
                Attachment.user_id,
                Attachment.filename,
                Attachment.content_type,
                Attachment.size_bytes,
                Attachment.created_at,
            )
        )
        .where(Attachment.user_id == user_id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
    )
    return list(db.execute(query).scalars().all())


def get_attachment_for_user(*, db: Session, attachment_id: str, user_id: str) -> Attachment | None:
    return db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.user_id == user_id,
        )
    )
