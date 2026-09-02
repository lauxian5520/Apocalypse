"""The session event envelope.

Everything the model sees must be reconstructable from this log — that is the
whole point of the subsystem. Events are append-only and carry a monotonic
`seq` per session, so a reader can replay a run exactly as it happened.
"""
from dataclasses import dataclass, field
from typing import Any

SESSION_FORMAT_VERSION = 1

# ── Event types ──────────────────────────────────────────────────
# Slash-namespaced, matching the upstream harness vocabulary.

SESSION_START = "session/start"
SESSION_END_SEED = "session/end-seed"   # boundary between forked history and new work

TURN_START = "turn/start"
TURN_END = "turn/end"
STEP_START = "step/start"
STEP_END = "step/end"

USER_MESSAGE = "user/message"
ASSISTANT_CHUNK = "assistant/chunk"     # raw stream fragments, kept for replay fidelity
ASSISTANT_MESSAGE = "assistant/message"

TOOL_CALL = "tool/call"
TOOL_APPROVAL = "tool/approval"
TOOL_RESULT = "tool/result"

LLM_USAGE = "llm/usage"
COMPACTION_SUMMARY = "compaction/summary"

AGENT_ERROR = "agent/error"
AGENT_INTERRUPT = "agent/interrupt"
CONFIG_CHANGE = "config/change"

# Only these three project into model messages. Every other type is log-only:
# it exists for durability, replay and the trajectory view, and never reaches
# the provider.
SURFACE_TYPES = frozenset({USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT})

# Types a reader is allowed to skip when it does not recognise them. Anything
# outside this set must abort reconstruction rather than silently drop context.
# CONFIG_CHANGE is deliberately absent: it carries the system-prompt snapshot,
# so a reader that skipped it would rebuild a request the model never saw.
IGNORABLE_TYPES = frozenset({
    ASSISTANT_CHUNK, LLM_USAGE, STEP_START, STEP_END,
    TURN_START, TURN_END, SESSION_START,
})

KNOWN_TYPES = frozenset({
    SESSION_START, SESSION_END_SEED, TURN_START, TURN_END, STEP_START, STEP_END,
    USER_MESSAGE, ASSISTANT_CHUNK, ASSISTANT_MESSAGE, TOOL_CALL, TOOL_APPROVAL,
    TOOL_RESULT, LLM_USAGE, COMPACTION_SUMMARY, AGENT_ERROR, AGENT_INTERRUPT,
    CONFIG_CHANGE,
})


@dataclass(frozen=True)
class SessionEvent:
    """One entry in a session log."""

    type: str
    seq: int
    time: int                              # unix epoch milliseconds
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ignorable(self) -> bool:
        """Whether a reader may skip this event without corrupting the run."""
        return self.type in IGNORABLE_TYPES

    def to_dict(self) -> dict:
        return {"type": self.type, "seq": self.seq, "time": self.time, "data": self.data}


def now_ms() -> int:
    import time
    return int(time.time() * 1000)
