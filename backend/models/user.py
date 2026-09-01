from datetime import datetime
from sqlalchemy import String, DateTime, Enum, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    role: Mapped[str] = mapped_column(
        Enum("user", "admin", name="user_role"), default="user", nullable=False
    )
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    memos: Mapped[list["Memo"]] = relationship(  # noqa: F821
        "Memo", back_populates="author", cascade="all, delete-orphan"
    )
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821
        "Comment", back_populates="author", cascade="all, delete-orphan"
    )
    sent_messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message", back_populates="sender", cascade="all, delete-orphan", foreign_keys="Message.sender_id"
    )
    received_messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message", back_populates="recipient", cascade="all, delete-orphan", foreign_keys="Message.recipient_id"
    )
