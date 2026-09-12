"""What the agent actually did, read out of the event log.

Every verifier except `outcome.py` needs to know the *shape* of a trajectory —
what was searched, what was opened, what was cited, how it ended. Each of them
parsing the log itself would mean four slightly different opinions about, say,
whether a failed `corpus_answer` counts as an answer. So the log is parsed once,
here, and the verifiers read fields off a dataclass.

This is the only module allowed to know the event vocabulary. It reads the log
exactly as `derive_messages()` does — from the same append-only list — which is
what makes a reward reproducible from a stored trajectory months later with no
model and no corpus.

One subtlety worth knowing: **a trajectory can contain several `corpus_answer`
calls.** `stops_turn` is honoured only when the tool did *not* error
(`harness/loop/agent.py:279`), so a call rejected for a malformed `citations`
argument leaves the loop running and the model free to try again. The last
*successful* one is the answer; the earlier failures are counted so the reward
can charge for them, otherwise format feedback becomes a free retry.
"""
import json
import re
from dataclasses import dataclass, field

from harness import events as ev
from harness.events import SessionEvent

ANSWER_TOOL = "corpus_answer"
SEARCH_TOOL = "corpus_search"
OPEN_TOOL = "corpus_open"

# Document ids are 12 hex characters (`rl/corpus/hotpot.py::doc_id_for`). Ids
# reach the model only inside rendered tool output, so scanning that text is how
# provenance is established — there is no structured channel to read instead.
DOC_ID = re.compile(r"\b[0-9a-f]{12}\b")


@dataclass
class ToolCall:
    name: str
    arguments: dict
    raw_arguments: str
    content: str = ""
    is_error: bool = False


@dataclass
class Trace:
    """One trajectory, flattened."""

    calls: list[ToolCall] = field(default_factory=list)
    steps: int = 0
    ended_by: str = ""              # "answer" | "max-steps" | "no-tool-call" | "error" | "interrupt"

    # ── what the model saw and did ────────────────────────────────
    queries: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    # Every doc id that ever appeared in a tool result — the set the model could
    # legitimately know about.
    seen_doc_ids: set[str] = field(default_factory=set)

    # ── the answer ────────────────────────────────────────────────
    answer: str | None = None       # None when the agent never answered
    citations: list[str] = field(default_factory=list)
    failed_answer_attempts: int = 0

    @property
    def answered(self) -> bool:
        return self.answer is not None

    @property
    def tool_calls(self) -> int:
        return len(self.calls)

    @property
    def searches(self) -> int:
        return len(self.queries)


def _parse_args(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        # Malformed arguments are a real model behaviour and the registry
        # already turned them into an error result. The trace records the call
        # happened with no usable arguments rather than dropping it, so the
        # format verifier can still charge for it.
        return {}


def extract(log: list[SessionEvent]) -> Trace:
    trace = Trace()

    results: dict[str, SessionEvent] = {
        e.data.get("tool_call_id", ""): e for e in log if e.type == ev.TOOL_RESULT
    }

    for event in log:
        if event.type == ev.STEP_START:
            trace.steps += 1
            continue

        if event.type == ev.AGENT_ERROR:
            trace.ended_by = "max-steps" if event.data.get("stage") == "max-steps" else "error"
            continue

        if event.type == ev.AGENT_INTERRUPT:
            trace.ended_by = "interrupt"
            continue

        if event.type != ev.ASSISTANT_MESSAGE:
            continue

        raw_calls = event.data.get("tool_calls") or []
        if not raw_calls and not trace.ended_by:
            # The loop breaks when the model stops asking for tools. Reaching
            # here without an answer is the "gave up" case, and it is one of the
            # most informative training-health signals at small scale.
            trace.ended_by = "no-tool-call"

        for raw in raw_calls:
            fn = raw.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "") or "{}"
            result = results.get(raw.get("id", ""))

            call = ToolCall(
                name=name,
                arguments=_parse_args(raw_args),
                raw_arguments=raw_args,
                content=(result.data.get("content", "") if result else ""),
                is_error=bool(result.data.get("is_error")) if result else False,
            )
            trace.calls.append(call)

            if call.content:
                trace.seen_doc_ids.update(DOC_ID.findall(call.content))

            if name == SEARCH_TOOL:
                query = call.arguments.get("query")
                if isinstance(query, str):
                    trace.queries.append(query)
            elif name == OPEN_TOOL:
                did = call.arguments.get("doc_id")
                if isinstance(did, str):
                    trace.opened.append(did)
            elif name == ANSWER_TOOL:
                if call.is_error:
                    trace.failed_answer_attempts += 1
                    continue
                answer = call.arguments.get("answer")
                trace.answer = answer if isinstance(answer, str) else ""
                cites = call.arguments.get("citations")
                trace.citations = [c for c in cites if isinstance(c, str)] if isinstance(cites, list) else []
                trace.ended_by = "answer"

    return trace
