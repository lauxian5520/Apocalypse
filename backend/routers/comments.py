"""Comments on a memo. Nested under /memos to match the resource hierarchy."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, selectinload

from core.deps import get_db, require_user
from core.errors import NotFoundError, PermissionError_, ValidationError
from models.comment import Comment
from models.memo import Memo
from models.user import User
from schemas.comment import CommentOut
from schemas.common import AuthorOut
from services import storage_service

router = APIRouter(prefix="/memos", tags=["comments"])


def _to_out(comment: Comment) -> CommentOut:
    return CommentOut(
        id=comment.id,
        memo_id=comment.memo_id,
        user_id=comment.user_id,
        author=AuthorOut(
            id=comment.author.id,
            username=comment.author.username,
            avatar_url=comment.author.avatar_url,
        ),
        content=comment.content,
        image_url=comment.image_url,
        created_at=comment.created_at,
    )


@router.post("/{memo_id}/comments", response_model=CommentOut, status_code=201)
async def add_comment(
    memo_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not db.query(Memo).filter(Memo.id == memo_id).first():
        raise NotFoundError("帖子不存在")

    content = ""
    image_url = None

    if "multipart/form-data" in (request.headers.get("content-type") or "").lower():
        form = await request.form()
        content = str(form.get("content") or "").strip()
        image = form.get("image")
        if image is not None and getattr(image, "filename", None):
            image_url = (await storage_service.save_comment_image(image, memo_id)).url
    else:
        body = await request.json()
        content = str((body or {}).get("content") or "").strip()

    if not content and not image_url:
        raise ValidationError("评论内容和图片不能同时为空")

    comment = Comment(
        memo_id=memo_id,
        user_id=user.id,
        content=content or "[图片]",
        image_url=image_url,
    )
    db.add(comment)
    db.commit()

    comment = (
        db.query(Comment)
        .options(selectinload(Comment.author))
        .filter(Comment.id == comment.id)
        .first()
    )
    return _to_out(comment)


@router.delete("/{memo_id}/comments/{comment_id}", status_code=204)
def delete_comment(
    memo_id: int,
    comment_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    comment = (
        db.query(Comment)
        .filter(Comment.id == comment_id, Comment.memo_id == memo_id)
        .first()
    )
    if not comment:
        raise NotFoundError("评论不存在")

    memo = db.query(Memo).filter(Memo.id == memo_id).first()
    # The comment author, the memo owner and admins may all remove it.
    allowed = (
        comment.user_id == user.id
        or (memo is not None and memo.user_id == user.id)
        or user.role == "admin"
    )
    if not allowed:
        raise PermissionError_("无权限")

    if comment.image_url:
        import os
        storage_service.delete_file(
            storage_service.settings.upload_dir, os.path.basename(comment.image_url)
        )
    db.delete(comment)
    db.commit()
