"""Memo CRUD."""
import json
import logging
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as FormUploadFile

from core.database import track_file
from core.deps import get_db, optional_user, require_user
from core.errors import NotFoundError, PermissionError_, ValidationError
from models.attachment import Attachment
from models.memo import Memo
from models.memo_like import MemoLike
from models.user import User
from schemas.common import Page
from schemas.memo import MemoLikesOut, MemoOut, MemoUpdateIn
from services import memo_service, storage_service

router = APIRouter(prefix="/memos", tags=["memos"])
logger = logging.getLogger(__name__)

MAX_IMAGES_PER_MEMO = 9


def _parse_form_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("", response_model=Page[MemoOut])
def list_memos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    visibility: Literal["public", "private"] = Query("public"),
    q: str = Query("", max_length=200),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    total, memos = memo_service.list_memos(db, visibility=visibility, page=page, page_size=page_size, viewer=viewer, q=q)
    return Page[MemoOut](
        total=total,
        page=page,
        page_size=page_size,
        items=[memo_service.to_out(m, viewer) for m in memos],
    )


@router.post("", status_code=201, response_model=MemoOut)
async def create_memo(
    content: str = Form(...),
    location: str = Form(""),
    visibility: Literal["public", "private"] = Form("public"),
    is_anonymous: str = Form("false"),
    images: list[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo = Memo(
        user_id=user.id,
        content=content,
        location=(location.strip() or None),
        visibility=visibility,
        is_anonymous=_parse_form_bool(is_anonymous),
    )
    db.add(memo)
    db.flush()      # assigns memo.id, needed to name the attachments

    for image in [i for i in images if i.filename][:MAX_IMAGES_PER_MEMO]:
        stored = await storage_service.save_memo_image(image, memo.id)
        track_file(db, stored.path)
        db.add(
            Attachment(
                memo_id=memo.id,
                original_name=stored.original_name,
                stored_filename=stored.filename,
                mime_type=stored.content_type,
                size_bytes=stored.size_bytes,
            )
        )

    db.commit()
    return memo_service.to_out(memo_service.load_memo(memo.id, db, user), user)


@router.get("/{memo_id}", response_model=MemoOut)
def get_memo(
    memo_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    return memo_service.to_out(memo_service.load_memo(memo_id, db, viewer), viewer)


@router.patch("/{memo_id}", response_model=MemoOut)
async def update_memo(
    memo_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo = db.query(Memo).filter(Memo.id == memo_id).with_for_update().first()
    if not memo:
        raise NotFoundError("帖子不存在")
    if not memo_service.can_modify(memo, user):
        raise PermissionError_("无权限")

    # Keep JSON PATCH clients working; multipart adds image editing atomically.
    images = []
    try:
        if "multipart/form-data" in request.headers.get("content-type", "").lower():
            form = await request.form()
            payload = {key: form[key] for key in MemoUpdateIn.model_fields if key in form}
            if "remove_attachment_ids" in payload:
                payload["remove_attachment_ids"] = json.loads(payload["remove_attachment_ids"])
            images = form.getlist("images")
            if any(not isinstance(image, FormUploadFile) for image in images):
                raise ValidationError("图片字段必须是文件")
            images = [image for image in images if image.filename]
        else:
            payload = await request.json()
        body = MemoUpdateIn.model_validate(payload)
    except (json.JSONDecodeError, TypeError):
        raise ValidationError("请求内容或图片移除列表格式无效")
    except SchemaValidationError as error:
        raise RequestValidationError(error.errors()) from error

    remove_ids = set(body.remove_attachment_ids)
    attachments = {attachment.id: attachment for attachment in memo.attachments}
    if not remove_ids.issubset(attachments):
        raise ValidationError("只能移除此笔记已有的图片，请刷新后重试")
    if len(attachments) - len(remove_ids) + len(images) > MAX_IMAGES_PER_MEMO:
        raise ValidationError("每条笔记最多保留 9 张图片")

    # New files are rolled back by get_db if any upload or the transaction fails.
    for image in images:
        stored = await storage_service.save_memo_image(image, memo.id)
        track_file(db, stored.path)
        memo.attachments.append(Attachment(
            original_name=stored.original_name,
            stored_filename=stored.filename,
            mime_type=stored.content_type,
            size_bytes=stored.size_bytes,
        ))
    removed_files = [attachments[attachment_id].stored_filename for attachment_id in remove_ids]
    for attachment_id in remove_ids:
        memo.attachments.remove(attachments[attachment_id])

    updates = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"remove_attachment_ids"})
    if "location" in updates:
        updates["location"] = updates["location"].strip() or None
    for field, value in updates.items():
        setattr(memo, field, value)
    if images or remove_ids:
        memo.updated_at = func.now()

    db.commit()
    # Original files stay intact until the replacement has committed successfully.
    for filename in removed_files:
        try:
            storage_service.delete_file(storage_service.settings.upload_dir, filename)
        except OSError:
            logger.warning("Could not remove detached memo image %s", filename, exc_info=True)
    return memo_service.to_out(memo_service.load_memo(memo_id, db, user), user)


@router.put("/{memo_id}/like", response_model=MemoLikesOut)
def like_memo(
    memo_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo_service.load_memo(memo_id, db, user)
    if db.get(MemoLike, (memo_id, user.id)) is None:
        db.add(MemoLike(memo_id=memo_id, user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # A simultaneous request from the same account may have liked it first.
            if db.get(MemoLike, (memo_id, user.id)) is None:
                raise
    return memo_service.likes_out(memo_service.load_memo(memo_id, db, user), user)


@router.delete("/{memo_id}/like", response_model=MemoLikesOut)
def unlike_memo(
    memo_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo_service.load_memo(memo_id, db, user)
    db.query(MemoLike).filter(MemoLike.memo_id == memo_id, MemoLike.user_id == user.id).delete()
    db.commit()
    return memo_service.likes_out(memo_service.load_memo(memo_id, db, user), user)


@router.delete("/{memo_id}", status_code=204)
def delete_memo(
    memo_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo = db.query(Memo).filter(Memo.id == memo_id).first()
    if not memo:
        raise NotFoundError("帖子不存在")
    if not memo_service.can_modify(memo, user):
        raise PermissionError_("无权限")

    for attachment in memo.attachments:
        storage_service.delete_file(storage_service.settings.upload_dir, attachment.stored_filename)
    db.delete(memo)
    db.commit()
