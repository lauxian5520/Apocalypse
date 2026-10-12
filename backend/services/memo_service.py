"""Memo reading rules: eager loading and the anonymity-aware projection.

This is the logic four endpoints share (list, get, create, update); keeping it
here stops each of them from re-deriving who is allowed to see an author.
"""
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from core.errors import NotFoundError, PermissionError_
from models.comment import Comment
from models.memo import Memo
from models.user import User
from schemas.comment import CommentOut
from schemas.common import AuthorOut
from schemas.memo import AttachmentOut, MemoOut

ANONYMOUS_AUTHOR = AuthorOut(id=None, username="匿名用户", avatar_url=None)


def attachment_url(stored_filename: str) -> str:
    return f"/api/uploads/{stored_filename}"


def _with_relations(query):
    return query.options(
        selectinload(Memo.author),
        selectinload(Memo.attachments),
        selectinload(Memo.comments).selectinload(Comment.author),
    )


def readable_clause(viewer: User | None):
    if viewer and viewer.role == "admin":
        return True
    return or_(Memo.visibility == "public", Memo.user_id == viewer.id) if viewer else Memo.visibility == "public"


def can_read(memo: Memo, viewer: User | None) -> bool:
    return memo.visibility == "public" or bool(viewer and (viewer.id == memo.user_id or viewer.role == "admin"))


def require_read(memo: Memo, viewer: User | None) -> None:
    if not can_read(memo, viewer):
        raise NotFoundError("帖子不存在")


def load_memo(memo_id: int, db: Session, viewer: User | None = None) -> Memo:
    memo = _with_relations(db.query(Memo)).filter(Memo.id == memo_id).first()
    if not memo:
        raise NotFoundError("帖子不存在")
    require_read(memo, viewer)
    return memo


def list_memos(db: Session, *, visibility: str, page: int, page_size: int, viewer: User | None = None) -> tuple[int, list[Memo]]:
    if visibility == "private" and viewer is None:
        raise PermissionError_("请先登录")
    query = (
        _with_relations(db.query(Memo))
        .filter(Memo.visibility == visibility)
        .filter(readable_clause(viewer))
        .order_by(Memo.pinned.desc(), Memo.created_at.desc())
    )
    total = query.count()
    memos = query.offset((page - 1) * page_size).limit(page_size).all()
    return total, memos


def can_see_author(memo: Memo, viewer: User | None) -> bool:
    if not memo.is_anonymous:
        return True
    return bool(viewer and (viewer.id == memo.user_id or viewer.role == "admin"))


def can_modify(memo: Memo, user: User) -> bool:
    return memo.user_id == user.id or user.role == "admin"


def to_out(memo: Memo, viewer: User | None = None) -> MemoOut:
    """Project a Memo for `viewer`, hiding the author of anonymous posts."""
    if can_see_author(memo, viewer):
        author = AuthorOut(
            id=memo.author.id,
            username=memo.author.username,
            avatar_url=memo.author.avatar_url,
        )
        user_id = memo.user_id
    else:
        author = ANONYMOUS_AUTHOR
        user_id = None

    comments = [
        CommentOut(
            id=c.id,
            memo_id=c.memo_id,
            user_id=c.user_id,
            author=AuthorOut(
                id=c.author.id,
                username=c.author.username,
                avatar_url=c.author.avatar_url,
            ),
            content=c.content,
            image_url=c.image_url,
            created_at=c.created_at,
        )
        for c in memo.comments
    ]

    return MemoOut(
        id=memo.id,
        user_id=user_id,
        author=author,
        content=memo.content,
        location=memo.location,
        visibility=memo.visibility,
        is_anonymous=memo.is_anonymous,
        pinned=memo.pinned,
        created_at=memo.created_at,
        updated_at=memo.updated_at,
        attachments=[
            AttachmentOut(
                id=a.id,
                original_name=a.original_name,
                stored_filename=a.stored_filename,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                url=attachment_url(a.stored_filename),
            )
            for a in memo.attachments
        ],
        comments=comments,
        comment_count=len(comments),
    )
