"""In-process interrupt tokens, one per running turn.

Deliberately process-local: an interrupt only means anything to the worker
currently driving that turn, and a multi-process deployment would need the
signal routed to that worker anyway.

Scope note: an interrupt stops the loop at the next boundary — it does not
cancel a tool that is already executing. A long command is bounded by the
sandbox's own timeout instead.
"""
import asyncio

# session_id -> the token for the turn currently running under it. Absence
# means nothing is running, which is what makes `request` able to report
# whether it actually reached anyone.
_tokens: dict[str, asyncio.Event] = {}


def begin(session_id: str) -> asyncio.Event:
    """Register a starting turn with a fresh, unset token."""
    token = asyncio.Event()
    _tokens[session_id] = token
    return token


def request(session_id: str) -> bool:
    """Ask a running turn to stop. False when nothing is running."""
    token = _tokens.get(session_id)
    if token is None:
        return False
    token.set()
    return True


def is_set(session_id: str) -> bool:
    token = _tokens.get(session_id)
    return token is not None and token.is_set()


def finish(session_id: str) -> None:
    """Drop the token once the turn is over.

    Best-effort only: a turn killed by a client disconnect may never reach
    this. Whether a session is busy is answered from the event log by
    `harness.session.manager.reconcile_status`, not from this registry.
    """
    _tokens.pop(session_id, None)
