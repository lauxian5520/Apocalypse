"""Memo CRUD."""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from core.deps import get_db, optional_user, require_user
from core.errors import NotFoundError, PermissionError_
from models.attachment import Attachment
from models.memo import Memo
from models.user import User
from schemas.common import Page
from schemas.memo import MemoOut, MemoUpdateIn
from services import memo_service, storage_service

router = APIRouter(prefix="/memos", tags=["memos"])

MAX_IMAGES_PER_MEMO = 9


def _parse_form_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("", response_model=Page[MemoOut])
def list_memos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    visibility: str = Query("public"),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    total, memos = memo_service.list_memos(db, visibility=visibility, page=page, page_size=page_size)
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
    visibility: str = Form("public"),
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
    return memo_service.to_out(memo_service.load_memo(memo.id, db), user)


@router.get("/{memo_id}", response_model=MemoOut)
def get_memo(
    memo_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    return memo_service.to_out(memo_service.load_memo(memo_id, db), viewer)


@router.patch("/{memo_id}", response_model=MemoOut)
def update_memo(
    memo_id: int,
    body: MemoUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    memo = db.query(Memo).filter(Memo.id == memo_id).first()
    if not memo:
        raise NotFoundError("帖子不存在")
    if not memo_service.can_modify(memo, user):
        raise PermissionError_("无权限")

    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if "location" in updates:
        updates["location"] = updates["location"].strip() or None
    for field, value in updates.items():
        setattr(memo, field, value)

    db.commit()
    return memo_service.to_out(memo_service.load_memo(memo_id, db), user)


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
