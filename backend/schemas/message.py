from datetime import datetime

from pydantic import BaseModel

from schemas.common import AuthorOut, ORMModel


class MessageSendIn(BaseModel):
    recipient_id: int
    content: str


class MessageOut(ORMModel):
    id: int
    sender_id: int
    recipient_id: int
    content: str
    image_url: str | None = None
    attachment_url: str | None = None
    attachment_type: str | None = None
    attachment_name: str | None = None
    is_read: bool
    created_at: datetime


class LastMessageOut(BaseModel):
    id: int
    content: str
    created_at: datetime
    sender_id: int


class ConversationOut(BaseModel):
    user: AuthorOut
    last_message: LastMessageOut
    unread: int
