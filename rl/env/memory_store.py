"""An in-memory `SessionStore` — the rollout-time replacement for SQLite.

This is the first of the two seams the harness's Protocol design pays off on.
`SqliteSessionStore` is correct for a website, where a session is long-lived,
concurrent with others, and must survive a restart. It is the wrong shape for
RL, for three reasons that compound:

- **Cost per event.** The loop re-reads the whole log once per step
  (`agent.py::_request_model`) and once per pending tool call
  (`agent.py::_run_pending_calls`). Against SQLite that is a query each time;
  against a list it is a slice. A 12-step trajectory does dozens of full reads.
- **A real user row.** `manager.create()` requires a `users.id` foreign key, so
  driving the loop through the manager means either a fake account or a DB. A
  rollout has no user.
- **Durability is the wrong trade.** A training run produces hundreds of
  thousands of throwaway trajectories. The ones worth keeping are written once,
  deliberately, as trajectory records — not fsynced event by event.

What it must *not* change is the log itself. `derive_messages()` is a pure
function of the event list, so a trajectory replayed from this store and the
same trajectory replayed from SQLite have to produce byte-identical messages.
That equivalence is asserted in the check script rather than assumed: it is the
premise the whole training pipeline rests on, and it is cheap to verify.
"""
import itertools
import threading

from harness.events import SessionEvent, now_ms


class MemorySessionStore:
    """Append-only event logs held in a dict, keyed by session id.

    Thread-safe because a rollout engine may drive many sessions from a thread
    pool, and `itertools.count` is not atomic under the GIL for the
    read-modify-write that assigning a sequence number really is. The lock is
    uncontended in the common single-loop case.
    """

    def __init__(self) -> None:
        self._logs: dict[str, list[SessionEvent]] = {}
        self._lock = threading.Lock()

    # ── SessionStore protocol ─────────────────────────────────────
    def append(self, session_id: str, type: str, data: dict) -> SessionEvent:
        with self._lock:
            log = self._logs.setdefault(session_id, [])
            event = SessionEvent(type=type, seq=len(log), time=now_ms(), data=data)
            log.append(event)
            return event

    def append_many(self, session_id: str, entries: list[tuple[str, dict]]) -> list[SessionEvent]:
        """One batch, contiguous sequence numbers, same ordering guarantee."""
        with self._lock:
            log = self._logs.setdefault(session_id, [])
            base = len(log)
            stamp = now_ms()
            events = [
                SessionEvent(type=t, seq=base + i, time=stamp, data=d)
                for i, (t, d) in enumerate(entries)
            ]
            log.extend(events)
            return events

    def read(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        with self._lock:
            log = self._logs.get(session_id)
            if not log:
                return []
            # Sequence numbers are dense list indices here, so the slice is
            # exact rather than a filter. Returning a copy keeps a caller from
            # mutating the log it was handed.
            return log[after_seq + 1:] if after_seq >= 0 else list(log)

    def next_seq(self, session_id: str) -> int:
        with self._lock:
            return len(self._logs.get(session_id, ()))

    # ── rollout conveniences ──────────────────────────────────────
    def sessions(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def drop(self, session_id: str) -> None:
        """Forget one session. A long run must not accumulate every trajectory."""
        with self._lock:
            self._logs.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._logs)


_counter = itertools.count()


def new_session_id(prefix: str = "rollout") -> str:
    """A unique id for one episode.

    Uniqueness matters beyond bookkeeping: `harness/loop/interrupt.py` keys a
    module-global dict by session id, so two concurrent rollouts sharing an id
    would interrupt each other. A process-local counter is enough — and unlike
    uuid4 it makes a trajectory's position in the run readable at a glance.
    """
    return f"{prefix}-{next(_counter):08d}"
