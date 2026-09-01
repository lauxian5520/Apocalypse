from datetime import datetime
from sqlalchemy import Text, ForeignKey, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    memo_id: Mapped[int] = mapped_column(
        ForeignKey("memos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    memo: Mapped["Memo"] = relationship("Memo", back_populates="comments")  # noqa: F821
    author: Mapped["User"] = relationship("User", back_populates="comments")  # noqa: F821
