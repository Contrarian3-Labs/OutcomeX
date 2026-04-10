from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.attachment import AttachmentResponse
from app.services.attachments import AttachmentTooLargeError, create_attachment, get_attachment, list_attachments

router = APIRouter()


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    user_id: str = Form(..., min_length=1),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> AttachmentResponse:
    payload = await file.read()
    try:
        attachment = create_attachment(
            db=db,
            user_id=user_id.strip(),
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
    user_id: str,
    db: Session = Depends(get_db),
) -> list[AttachmentResponse]:
    attachments = list_attachments(db=db, user_id=user_id)
    return [AttachmentResponse.model_validate(item) for item in attachments]


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: str, db: Session = Depends(get_db)) -> Response:
    attachment = get_attachment(db=db, attachment_id=attachment_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.filename}"',
        },
    )
