"""The session-log seam.

`SessionStore` is the interface the rest of the harness talks to. Swapping the
default SQLite implementation for a JSONL one is a one-line change in
`harness/context.py` — nothing else in the subsystem knows where events live.
"""
from typing import Protocol, runtime_checkable

from harness.events import SessionEvent


@runtime_checkable
class SessionStore(Protocol):
    """Append-only event log, addressed by session id."""

    def append(self, session_id: str, type: str, data: dict) -> SessionEvent:
        """Claim the next seq and durably record one event."""
        ...

    def append_many(self, session_id: str, entries: list[tuple[str, dict]]) -> list[SessionEvent]:
        """Write several events in one transaction, claiming consecutive seqs."""
        ...

    def read(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        """Every event with `seq > after_seq`, in order."""
        ...

    def next_seq(self, session_id: str) -> int:
        """The seq the next append will claim."""
        ...
