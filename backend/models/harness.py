"""Harness persistence: a session header and its append-only event log."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class HarnessSession(Base):
    __tablename__ = "harness_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)   # uuid4().hex
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    preset: Mapped[str] = mapped_column(String(40), default="standard", nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("idle", "running", "awaiting_approval", "error", name="harness_session_status"),
        default="idle",
        nullable=False,
    )
    # Set when this session was branched off another one, so the UI can show
    # where a trajectory came from.
    forked_from: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    forked_at_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Set when this session is a subagent run dispatched by another one. A fork
    # copies history; a delegation does not, so the two cannot share a column.
    # Deliberately unindexed: these arrive by ALTER TABLE on deployed databases,
    # which adds no index, and declaring one here would describe a schema that
    # does not exist.
    parent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    agent: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    events: Mapped[list["HarnessEvent"]] = relationship(  # noqa: F821
        "HarnessEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="HarnessEvent.seq",
    )


class HarnessEvent(Base):
    """One immutable log entry. Nothing ever updates a row in this table."""

    __tablename__ = "harness_events"
    __table_args__ = (
        # The append-only integrity guarantee: a seq is claimed exactly once
        # per session, so two concurrent writers cannot interleave silently.
        Index("ix_harness_events_session_seq", "session_id", "seq", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("harness_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    time: Mapped[int] = mapped_column(Integer, nullable=False)   # unix epoch ms
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # JSON

    # Relationships
    session: Mapped["HarnessSession"] = relationship(  # noqa: F821
        "HarnessSession", back_populates="events"
    )
