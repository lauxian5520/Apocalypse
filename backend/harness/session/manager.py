"""Session lifecycle: create, list, fork, delete, and the workspace behind each.

The event log is immutable; this module owns the mutable header row that points
at it — ownership, title, status — plus the workspace directory on disk.
"""
import logging
import shutil
import uuid

from sqlalchemy import func, or_, select

from core.database import SessionLocal
from core.errors import NotFoundError, PermissionError_, ValidationError
from harness import events as ev
from harness.events import now_ms
from harness.llm import pricing
from harness.sandbox.workspace import Workspace
from harness.session.sqlite_store import SqliteSessionStore
from models.harness import HarnessEvent, HarnessSession

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
STATUSES = ("idle", "running", "awaiting_approval", "error")


# Events that end a turn, mapped to the status each implies.
_TERMINAL_STATUS = {
    ev.TOOL_APPROVAL: "awaiting_approval",
    ev.AGENT_ERROR: "error",
    ev.TURN_END: "idle",
    ev.AGENT_INTERRUPT: "idle",
}

# How long a turn may go without logging anything before it is presumed dead.
# The longest legitimate gap is one model request that streams no text before
# its tool calls; the adapter caps that at STREAM_TIMEOUT_SECONDS (300s), and
# in practice reasoning and content chunks arrive continuously. A turn quieter
# than this window has lost its worker.
LIVENESS_WINDOW_MS = 180_000


def _turn_outcome(session_id: str) -> tuple[str | None, int]:
    """How the newest turn ended, plus the log's last timestamp.

    Returns `(status, last_event_time)` where status is None while the turn is
    still open. The query looks at terminal events *and* `turn/start` together
    and takes the newest: within one turn `turn/start` always precedes its
    terminal event, so if the newest of the two is `turn/start` the turn has
    not ended yet, and a terminal event from an earlier turn cannot be
    mistaken for this one's.

    That distinction matters because the loop emits `step/end` *after*
    `tool/approval` — reading only the single last event reported a session
    waiting on a human as still running.
    """
    interesting = list(_TERMINAL_STATUS) + [ev.TURN_START]
    with SessionLocal() as db:
        marker = db.execute(
            select(HarnessEvent.type)
            .where(HarnessEvent.session_id == session_id,
                   HarnessEvent.type.in_(interesting))
            .order_by(HarnessEvent.seq.desc())
            .limit(1)
        ).first()
        latest = db.execute(
            select(HarnessEvent.time)
            .where(HarnessEvent.session_id == session_id)
            .order_by(HarnessEvent.seq.desc())
            .limit(1)
        ).first()

    last_time = latest.time if latest else 0
    if marker is None:
        return "idle", last_time
    return _TERMINAL_STATUS.get(marker.type), last_time


def reconcile_status(row: HarnessSession) -> HarnessSession:
    """Repair a `running` row whose turn is no longer running.

    Needed because a disconnected client leaves the streaming generator to be
    finalized whenever the event loop gets to it — which can be long after the
    tab closed, or never — and even the background-task cleanup lands slightly
    after the response, so a client reading straight back would see the stale
    value. The status is therefore recovered from the log, which is the
    authority here for the same reason it is everywhere else in this
    subsystem: it is the only state that survives a lost worker.
    """
    if row.status != "running":
        return row

    healed, last_time = _turn_outcome(row.id)
    if healed is None:
        # The turn is still open. Believe that only while the log is moving;
        # a silent one has lost its worker.
        if now_ms() - last_time <= LIVENESS_WINDOW_MS:
            return row
        healed = "idle"

    logger.info("[harness] healing stale status on %s: running -> %s", row.id, healed)
    set_status(row.id, healed)
    row.status = healed
    return row


def finalize_turn(session_id: str) -> str:
    """Settle a session's status once its turn is definitively over.

    Called from a Starlette background task, which runs after the response
    completes *including* when the client disconnected — unlike the streaming
    generator's own `finally`, which waits for the event loop to finalize a
    suspended async generator and may never run in time.
    """
    # None means this turn logged no terminal event — it was cut off
    # mid-flight. The session is idle either way, and the log already records
    # exactly how far it got.
    status, _ = _turn_outcome(session_id)
    status = status or "idle"
    set_status(session_id, status)
    return status


def reset_running_sessions() -> int:
    """Settle every session left `running` by a stopped process. Returns the count.

    A turn cannot outlive the worker driving it, so at startup nothing is
    running by definition. Without this, sessions interrupted by a restart sit
    at `running` until the liveness window expires — several minutes of a
    spinner for something already known to be over.
    """
    with SessionLocal() as db:
        stranded = list(db.scalars(
            select(HarnessSession).where(HarnessSession.status == "running")
        ).all())
        for row in stranded:
            db.expunge(row)

    for row in stranded:
        healed, _ = _turn_outcome(row.id)
        set_status(row.id, healed or "idle")
    if stranded:
        logger.info("[harness] reset %d session(s) left running by a previous process",
                    len(stranded))
    return len(stranded)


def is_busy(session_id: str) -> bool:
    """Whether a turn is genuinely still running, after reconciliation."""
    with SessionLocal() as db:
        row = db.get(HarnessSession, session_id)
        if row is None:
            return False
        db.expunge(row)
    return reconcile_status(row).status == "running"


def create(user_id: int, preset: str, title: str = "") -> HarnessSession:
    session_id = uuid.uuid4().hex
    with SessionLocal() as db:
        row = HarnessSession(id=session_id, user_id=user_id, preset=preset, title=title)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)

    Workspace(session_id).ensure()
    SqliteSessionStore().append(session_id, ev.SESSION_START, {"preset": preset})
    return row


def get_owned(session_id: str, user_id: int) -> HarnessSession:
    """The session, or an error. Ownership is checked here so routers cannot forget."""
    with SessionLocal() as db:
        row = db.get(HarnessSession, session_id)
        if row is None:
            raise NotFoundError("会话不存在")
        if row.user_id != user_id:
            raise PermissionError_("无权访问该会话")
        db.expunge(row)
    return reconcile_status(row)


def list_for_user(user_id: int, query: str = "", page: int = 1, page_size: int = 0) -> tuple:
    """`(total, rows)` for one user, newest first, optionally filtered."""
    page = max(1, page)
    page_size = min(page_size or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)

    with SessionLocal() as db:
        stmt = select(HarnessSession).where(HarnessSession.user_id == user_id)
        if query.strip():
            like = f"%{query.strip()}%"
            # Search titles and what the user actually typed. Assistant text and
            # tool output are excluded on purpose: they are long, and matching
            # them makes every session look relevant.
            matching_ids = (
                select(HarnessEvent.session_id)
                .where(HarnessEvent.type == ev.USER_MESSAGE, HarnessEvent.data.like(like))
                .scalar_subquery()
            )
            stmt = stmt.where(or_(
                HarnessSession.title.like(like),
                HarnessSession.id.in_(matching_ids),
            ))

        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.scalars(
            stmt.order_by(HarnessSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        for row in rows:
            db.expunge(row)
    return total, [reconcile_status(row) for row in rows]


def delete(session_id: str, user_id: int) -> None:
    get_owned(session_id, user_id)
    with SessionLocal() as db:
        row = db.get(HarnessSession, session_id)
        if row is not None:
            db.delete(row)        # events cascade
            db.commit()
    Workspace(session_id).destroy()


def fork(session_id: str, user_id: int, seq: int) -> HarnessSession:
    """Branch a new session off `session_id`, carrying history up to `seq`.

    The workspace is copied too — a trajectory without the files it produced
    would replay into a different world than the one it ran in.
    """
    origin = get_owned(session_id, user_id)
    store = SqliteSessionStore()
    history = [e for e in store.read(session_id) if e.seq <= seq]
    if not history:
        raise ValidationError("该位置之前没有可复制的历史")

    new_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(HarnessSession(
            id=new_id,
            user_id=user_id,
            preset=origin.preset,
            title=(origin.title or "会话") + "（分支）",
            forked_from=session_id,
            forked_at_seq=seq,
        ))
        db.add_all([
            HarnessEvent(
                session_id=new_id, seq=e.seq, type=e.type, time=e.time,
                data=_dump(e.data),
            )
            for e in history
        ])
        db.commit()

    # Marks where inherited history stops and new work begins.
    store.append(new_id, ev.SESSION_END_SEED, {"forked_from": session_id, "at_seq": seq})

    source = Workspace(session_id)
    target = Workspace(new_id)
    try:
        shutil.copytree(source.root, target.ensure(), dirs_exist_ok=True)
    except OSError as e:
        logger.warning("[harness] workspace copy failed for fork %s: %s", new_id, e)

    return get_owned(new_id, user_id)


def set_status(session_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValidationError(f"未知的会话状态：{status}")
    _update(session_id, status=status)


def set_title(session_id: str, title: str) -> None:
    _update(session_id, title=title[:200])


def touch(session_id: str) -> None:
    """Bump `updated_at` so the sidebar ordering follows activity."""
    _update(session_id)


def usage_summary(session_id: str) -> dict:
    """Token and cost totals rolled up from the session's `llm/usage` events."""
    events = SqliteSessionStore().read(session_id)
    return pricing.summarize([e.data for e in events if e.type == ev.LLM_USAGE])


def _update(session_id: str, **fields) -> None:
    with SessionLocal() as db:
        row = db.get(HarnessSession, session_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        # An UPDATE with no changed column still fires onupdate for updated_at.
        row.updated_at = func.now()
        db.commit()


def _dump(data: dict) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, default=str)
