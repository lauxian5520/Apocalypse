from datetime import datetime

from pydantic import BaseModel

from schemas.common import AuthorOut, ORMModel


class CommentIn(BaseModel):
    content: str


class CommentOut(ORMModel):
    id: int
    memo_id: int
    user_id: int
    author: AuthorOut
    content: str
    image_url: str | None = None
    created_at: datetime
