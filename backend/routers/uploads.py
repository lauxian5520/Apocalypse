"""Serve only database-owned files, enforcing the owning resource's ACL."""
import mimetypes
from PIL import Image, UnidentifiedImageError

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.deps import get_db, optional_user
from core.errors import NotFoundError
from models.attachment import Attachment
from models.comment import Comment
from models.message import Message
from models.user import User
from services import memo_service, storage_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/{filename:path}")
def serve_upload(filename: str, viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    path = storage_service.resolve_public_path(filename)
    if not path:
        raise NotFoundError("File not found")
    url = f"/api/uploads/{filename}"
    private = False
    download = False
    original = None
    attachment = db.query(Attachment).filter(Attachment.stored_filename == filename).first()
    comment = db.query(Comment).filter(Comment.image_url == url).first()
    message = db.query(Message).filter(or_(Message.image_url == url, Message.attachment_url == url)).first()
    avatar = db.query(User.id).filter(User.avatar_url == url).first()
    if message:
        if not viewer or viewer.id not in (message.sender_id, message.recipient_id):
            raise NotFoundError("File not found")
        private = True
        download = message.attachment_type not in storage_service.IMAGE_TYPES
        original = message.attachment_name or "attachment"
    elif attachment or comment:
        memo = attachment.memo if attachment else comment.memo
        memo_service.require_read(memo, viewer)
        private = memo.visibility == "private"
    elif not avatar:
        raise NotFoundError("File not found")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if media_type in storage_service.IMAGE_TYPES and not download:
        try:
            with Image.open(path) as image:
                actual = Image.MIME.get(image.format)
                image.verify()
            if actual not in storage_service.IMAGE_TYPES:
                raise ValueError("unsafe image")
            media_type = actual
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            download = True
    else:
        download = True
    return FileResponse(
        path, media_type=media_type if not download else "application/octet-stream",
        filename=(original or "attachment") if download else None,
        headers={"Cache-Control": "private, no-store" if private else "no-store",
                 "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )
