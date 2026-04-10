from __future__ import annotations

from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.attachments import MAX_ATTACHMENT_REQUEST_SIZE_BYTES

ATTACHMENT_LIMIT_EXCEEDED_DETAIL = "Attachment exceeds 25 MB size limit"


@dataclass
class _AttachmentBodyTooLargeError(Exception):
    """Raised when upload body size exceeds middleware request limit."""

    detail: str = ATTACHMENT_LIMIT_EXCEEDED_DETAIL


class AttachmentUploadSizeLimitMiddleware:
    """Guards upload request bytes before route parsing consumes multipart body."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        upload_path_suffix: str = "/attachments",
        request_limit_bytes: int = MAX_ATTACHMENT_REQUEST_SIZE_BYTES,
    ) -> None:
        self.app = app
        self.upload_path_suffix = upload_path_suffix
        self.request_limit_bytes = request_limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies_to_scope(scope):
            await self.app(scope, receive, send)
            return

        consumed_bytes = 0

        async def guarded_receive() -> Message:
            nonlocal consumed_bytes
            message = await receive()
            if message["type"] != "http.request":
                return message

            consumed_bytes += len(message.get("body", b""))
            if consumed_bytes > self.request_limit_bytes:
                raise _AttachmentBodyTooLargeError
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except _AttachmentBodyTooLargeError as exc:
            response = JSONResponse(
                status_code=413,
                content={"detail": exc.detail},
            )
            await response(scope, receive, send)

    def _applies_to_scope(self, scope: Scope) -> bool:
        if scope["type"] != "http":
            return False
        method = (scope.get("method") or "").upper()
        if method != "POST":
            return False
        path = scope.get("path") or ""
        if not path.endswith(self.upload_path_suffix):
            return False
        return not path.endswith(f"{self.upload_path_suffix}/sessions")
