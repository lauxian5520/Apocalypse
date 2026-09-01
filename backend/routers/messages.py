"""Direct messages between two users."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from core.deps import get_db, require_user
from core.errors import NotFoundError, ValidationError
from models.message import Message
from models.user import User
from schemas.common import AuthorOut
from schemas.message import ConversationOut, LastMessageOut, MessageOut, MessageSendIn
from services import storage_service

router = APIRouter(prefix="/messages", tags=["messages"])

_ATTACHMENT_LABELS = (("image/", "[图片]"), ("video/", "[视频]"))


def _between(a: int, b: int):
    """Filter matching messages in either direction between two users."""
    return or_(
        and_(Message.sender_id == a, Message.recipient_id == b),
        and_(Message.sender_id == b, Message.recipient_id == a),
    )


def _placeholder(content_type: str | None) -> str:
    if not content_type:
        return "[附件]"
    for prefix, label in _ATTACHMENT_LABELS:
        if content_type.startswith(prefix):
            return label
    return "[文件]"


@router.post("", response_model=MessageOut, status_code=201)
async def send_message(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    content = ""
    recipient_id = 0
    stored = None

    if "multipart/form-data" in (request.headers.get("content-type") or "").lower():
        form = await request.form()
        content = str(form.get("content") or "").strip()
        try:
            recipient_id = int(form.get("recipient_id") or 0)
        except (TypeError, ValueError):
            recipient_id = 0
        upload = form.get("file") or form.get("image")
        if upload is not None and getattr(upload, "filename", None):
            stored = await storage_service.save_message_attachment(upload, user.id)
    else:
        body = MessageSendIn.model_validate(await request.json())
        content = (body.content or "").strip()
        recipient_id = body.recipient_id

    if not content and stored is None:
        raise ValidationError("消息文本和附件不能同时为空")
    if recipient_id == user.id:
        raise ValidationError("不能给自己发私聊")

    recipient = db.query(User).filter(User.id == recipient_id).first()
    if not recipient:
        raise NotFoundError("接收方用户不存在")
    if recipient.is_disabled:
        raise ValidationError("该用户已被禁用")

    is_image = bool(stored and stored.content_type.startswith("image/"))
    message = Message(
        sender_id=user.id,
        recipient_id=recipient_id,
        content=content or _placeholder(stored.content_type if stored else None),
        image_url=stored.url if is_image else None,
        attachment_url=stored.url if stored else None,
        attachment_type=stored.content_type if stored else None,
        attachment_name=stored.original_name if stored else None,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("/conversations", response_model=dict)
def list_conversations(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Message.sender_id, Message.recipient_id)
        .filter(or_(Message.sender_id == current.id, Message.recipient_id == current.id))
        .all()
    )
    partner_ids = {
        recipient_id if sender_id == current.id else sender_id
        for sender_id, recipient_id in rows
    }

    items: list[ConversationOut] = []
    for peer_id in partner_ids:
        partner = db.query(User).filter(User.id == peer_id).first()
        if not partner:
            continue
        last = (
            db.query(Message)
            .filter(_between(current.id, peer_id))
            .order_by(Message.created_at.desc())
            .first()
        )
        if not last:
            continue
        unread = (
            db.query(func.count(Message.id))
            .filter(
                Message.sender_id == peer_id,
                Message.recipient_id == current.id,
                Message.is_read.is_(False),
            )
            .scalar()
            or 0
        )
        items.append(
            ConversationOut(
                user=AuthorOut(id=partner.id, username=partner.username, avatar_url=partner.avatar_url),
                last_message=LastMessageOut(
                    id=last.id,
                    content=last.content,
                    created_at=last.created_at,
                    sender_id=last.sender_id,
                ),
                unread=int(unread),
            )
        )

    items.sort(key=lambda c: c.last_message.created_at, reverse=True)
    return {"items": [i.model_dump(mode="json") for i in items]}


@router.get("/with/{user_id}", response_model=list[MessageOut])
def list_messages_with_user(
    user_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not db.query(User).filter(User.id == user_id).first():
        raise NotFoundError("用户不存在")
    return (
        db.query(Message)
        .filter(_between(current.id, user_id))
        .order_by(Message.created_at.asc())
        .all()
    )


@router.patch("/read/{user_id}")
def mark_read(
    user_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    db.query(Message).filter(
        Message.sender_id == user_id,
        Message.recipient_id == current.id,
        Message.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"message": "ok"}
