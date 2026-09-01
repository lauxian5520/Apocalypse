from datetime import datetime

from pydantic import BaseModel

from schemas.comment import CommentOut
from schemas.common import AuthorOut, ORMModel


class AttachmentOut(ORMModel):
    id: int
    original_name: str
    stored_filename: str
    mime_type: str
    size_bytes: int
    url: str


class MemoOut(ORMModel):
    id: int
    user_id: int | None
    author: AuthorOut
    content: str
    location: str | None = None
    visibility: str
    is_anonymous: bool
    pinned: bool
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentOut] = []
    comments: list[CommentOut] = []
    comment_count: int = 0


class MemoUpdateIn(BaseModel):
    content: str | None = None
    location: str | None = None
    visibility: str | None = None
    is_anonymous: bool | None = None
    pinned: bool | None = None
