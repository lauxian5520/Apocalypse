"""One durable like per user and memo."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class MemoLike(Base):
    __tablename__ = "memo_likes"

    memo_id: Mapped[int] = mapped_column(
        ForeignKey("memos.id", ondelete="CASCADE"), primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    memo: Mapped["Memo"] = relationship("Memo", back_populates="likes")  # noqa: F821
    user: Mapped["User"] = relationship("User", back_populates="memo_likes")  # noqa: F821
