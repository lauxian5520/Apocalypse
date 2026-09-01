from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base


class Memo(Base):
    __tablename__ = "memos"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    visibility: Mapped[str] = mapped_column(
        Enum("public", "private", name="memo_visibility"),
        default="public",
        nullable=False,
    )
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    author: Mapped["User"] = relationship("User", back_populates="memos")  # noqa: F821
    attachments: Mapped[list["Attachment"]] = relationship(  # noqa: F821
        "Attachment", back_populates="memo", cascade="all, delete-orphan"
    )
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821
        "Comment", back_populates="memo", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
