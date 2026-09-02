"""Default `SessionStore`: the project's SQLAlchemy database.

Each call opens and closes its own short DB session. An agent turn can run for
minutes, and holding one connection open across it would pin a SQLite writer
for the whole run — appends are independent commits anyway, which is exactly
what an append-only log wants.
"""
import json
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.database import SessionLocal
from core.errors import NotFoundError
from harness.events import SessionEvent, now_ms
from models.harness import HarnessEvent

logger = logging.getLogger(__name__)

# A concurrent append can lose the race for a seq. The unique index turns that
# into an IntegrityError rather than a silently interleaved log, so retry.
MAX_SEQ_RETRIES = 5


class SqliteSessionStore:
    """Append-only log backed by the `harness_events` table."""

    def __init__(self, session_factory: sessionmaker = SessionLocal):
        self._session_factory = session_factory

    def append(self, session_id: str, type: str, data: dict) -> SessionEvent:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        time_ms = now_ms()

        for attempt in range(MAX_SEQ_RETRIES):
            with self._session_factory() as db:
                seq = self._next_seq(db, session_id)
                db.add(HarnessEvent(
                    session_id=session_id, seq=seq, type=type, time=time_ms, data=payload,
                ))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    logger.debug("[harness] seq %s taken on %s, retrying", seq, session_id)
                    continue
                return SessionEvent(type=type, seq=seq, time=time_ms, data=data)

        raise NotFoundError(f"事件写入失败：会话 {session_id} 序号冲突未能解决")

    def append_many(self, session_id: str, entries: list[tuple[str, dict]]) -> list[SessionEvent]:
        """Write several events in one transaction, claiming consecutive seqs.

        Streamed chunks arrive one token at a time; committing each separately
        costs an fsync per token and dominated the wall time of a long
        response. One transaction per burst makes the log cheap enough to keep
        the raw fragments it needs for replay.
        """
        if not entries:
            return []

        time_ms = now_ms()
        for _ in range(MAX_SEQ_RETRIES):
            with self._session_factory() as db:
                start = self._next_seq(db, session_id)
                db.add_all([
                    HarnessEvent(
                        session_id=session_id, seq=start + i, type=type, time=time_ms,
                        data=json.dumps(data, ensure_ascii=False, default=str),
                    )
                    for i, (type, data) in enumerate(entries)
                ])
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    logger.debug("[harness] seq block from %s taken, retrying", start)
                    continue
                return [
                    SessionEvent(type=type, seq=start + i, time=time_ms, data=data)
                    for i, (type, data) in enumerate(entries)
                ]

        raise NotFoundError(f"事件写入失败：会话 {session_id} 序号冲突未能解决")

    def read(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        with self._session_factory() as db:
            rows = (
                db.query(HarnessEvent)
                .filter(HarnessEvent.session_id == session_id, HarnessEvent.seq > after_seq)
                .order_by(HarnessEvent.seq)
                .all()
            )
            return [_to_event(r) for r in rows]

    def next_seq(self, session_id: str) -> int:
        with self._session_factory() as db:
            return self._next_seq(db, session_id)

    @staticmethod
    def _next_seq(db, session_id: str) -> int:
        from sqlalchemy import func as sa_func
        highest = (
            db.query(sa_func.max(HarnessEvent.seq))
            .filter(HarnessEvent.session_id == session_id)
            .scalar()
        )
        return 0 if highest is None else int(highest) + 1


def _to_event(row: HarnessEvent) -> SessionEvent:
    try:
        data = json.loads(row.data)
    except (json.JSONDecodeError, TypeError):
        # A corrupt row must not take the whole trajectory down with it.
        logger.warning("[harness] unreadable event data at %s#%s", row.session_id, row.seq)
        data = {}
    return SessionEvent(type=row.type, seq=row.seq, time=row.time, data=data)
