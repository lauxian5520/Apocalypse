from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PositiveInt, field_validator

from schemas.comment import CommentOut
from schemas.common import AuthorOut, ORMModel


class AttachmentOut(ORMModel):
    id: int
    original_name: str
    stored_filename: str
    mime_type: str
    size_bytes: int
    url: str


class MemoLikesOut(ORMModel):
    likes: list[AuthorOut] = Field(default_factory=list)
    like_count: int = 0
    liked_by_me: bool = False


class MemoOut(MemoLikesOut):
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
    visibility: Literal["public", "private"] | None = None
    is_anonymous: bool | None = None
    pinned: bool | None = None
    remove_attachment_ids: list[PositiveInt] = Field(default_factory=list, max_length=9)

    @field_validator("content")
    @classmethod
    def nonempty_content(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("笔记内容不能为空")
        return value
