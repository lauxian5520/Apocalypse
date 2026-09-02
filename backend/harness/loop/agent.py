"""The agent loop: turn and step semantics over an append-only log.

Produces events and knows nothing about HTTP — SSE framing is the router's job.
That separation is what lets `tools/harness_probe.py` drive a complete run with
no server in sight.

Invariant: every event is written to the store *before* it is yielded. A client
that disconnects mid-turn loses the stream, never the history.
"""
import logging
import time
from typing import AsyncIterator

from core.errors import AppError
from harness import events as ev
from harness.events import SessionEvent
from harness.loop import interrupt
from harness.loop.hooks import PRE_EXECUTE, PRE_STEP
from harness.session.compaction import maybe_compact
from harness.session.projection import derive_messages, logged_system_prompt
from harness.tools.approval import ASK, DENY

logger = logging.getLogger(__name__)

DENIED_RESULT = "用户拒绝了这次工具调用。请换一种方式，或询问用户下一步怎么做。"
INTERRUPTED_RESULT = "用户中断了这次工具调用。"

# Streamed fragments are logged for replay fidelity, but a model emits them one
# token at a time and a commit each would cost an fsync per token — measured at
# roughly a fifth of a long turn's wall clock. They are buffered and written in
# bursts instead: whichever of these two limits comes first, so throughput
# improves without the text visibly stalling on its way to the browser.
CHUNK_FLUSH_SIZE = 32
CHUNK_FLUSH_SECONDS = 0.15


async def run_turn(hctx, user_text: str = "") -> AsyncIterator[SessionEvent]:
    """Start a new turn from user input."""
    interrupt.begin(hctx.session_id)
    try:
        if user_text:
            yield hctx.emit(ev.USER_MESSAGE, {"content": user_text})
        yield hctx.emit(ev.TURN_START, {})

        async for event in _drive(hctx):
            yield event
    finally:
        interrupt.finish(hctx.session_id)


async def resume_turn(hctx, call_id: str, approved: bool) -> AsyncIterator[SessionEvent]:
    """Continue a turn that stopped for approval."""
    log = hctx.store.read(hctx.session_id)
    call = _find_call(log, call_id)
    if call is None:
        raise AppError(f"找不到待批准的工具调用：{call_id}")

    interrupt.begin(hctx.session_id)
    try:
        if approved:
            name = call.get("function", {}).get("name", "")
            if name not in hctx.tools:
                raise AppError(f"本次会话没有启用工具 {name}")
            async for event in _execute(hctx, call, hctx.tools.get(name)):
                yield event
        else:
            yield hctx.emit(ev.TOOL_RESULT, {
                "tool_call_id": call_id,
                "name": call.get("function", {}).get("name", ""),
                "content": DENIED_RESULT,
                "is_error": True,
                "denied": True,
            })

        # Other calls from the same assistant message may still be waiting.
        async for event in _run_pending_calls(hctx):
            yield event
        async for event in _drive(hctx):
            yield event
    finally:
        interrupt.finish(hctx.session_id)


async def _drive(hctx) -> AsyncIterator[SessionEvent]:
    """Step until the model stops asking for anything."""
    for step in range(hctx.max_steps):
        if interrupt.is_set(hctx.session_id):
            yield hctx.emit(ev.AGENT_INTERRUPT, {"where": "step-boundary"})
            break

        rejection = hctx.hooks.emit(PRE_STEP, hctx, step)
        if rejection:
            yield hctx.emit(ev.AGENT_ERROR, {"message": str(rejection), "stage": "pre-step"})
            break

        yield hctx.emit(ev.STEP_START, {"step": step})

        try:
            result = None
            async for event in _request_model(hctx):
                if isinstance(event, SessionEvent):
                    yield event
                else:
                    result = event          # the assembled LLMResult
        except AppError as e:
            yield hctx.emit(ev.AGENT_ERROR, {"message": e.message, "stage": "request"})
            break

        if result is None:                  # interrupted mid-stream
            yield hctx.emit(ev.STEP_END, {"step": step, "stopped": True})
            break

        tool_calls = [c.to_wire() for c in result.tool_calls]
        yield hctx.emit(ev.ASSISTANT_MESSAGE, {
            "content": result.content,
            "reasoning": result.reasoning,
            "tool_calls": tool_calls,
            "finish_reason": result.finish_reason,
        })
        yield hctx.emit(ev.LLM_USAGE, result.usage.to_dict())

        if not tool_calls:
            yield hctx.emit(ev.STEP_END, {"step": step})
            break

        awaiting, halted = False, False
        async for event in _run_pending_calls(hctx):
            yield event
            if event.type == ev.TOOL_APPROVAL:
                awaiting = True
            elif event.type == ev.AGENT_INTERRUPT:
                halted = True
            elif event.type == ev.TOOL_RESULT and event.data.get("stops_turn"):
                halted = True

        yield hctx.emit(ev.STEP_END, {"step": step, "stopped": awaiting or halted})
        if awaiting:
            return                          # the turn stays open, awaiting a human
        if halted:
            break
    else:
        yield hctx.emit(ev.AGENT_ERROR, {
            "message": f"已达到本轮最大步数 {hctx.max_steps}，已停止",
            "stage": "max-steps",
        })

    yield hctx.emit(ev.TURN_END, {})


async def _request_model(hctx):
    """Stream one model response, yielding chunk events then the LLMResult."""
    log = await maybe_compact(hctx, hctx.store.read(hctx.session_id), hctx.context_budget)

    # Record the system prompt whenever it differs from the last one logged —
    # which happens on the first request of a session, the first request of a
    # new day (the runtime block carries the date), and after `system.md` is
    # edited. Writing it only on change keeps the log small while making every
    # request reconstructable, including which prompt was in force when.
    snapshot = _snapshot_prompt(hctx, log)
    if snapshot is not None:
        log = log + [snapshot]
        yield snapshot

    messages = derive_messages(log, hctx.system_prompt)

    buffer: list[tuple[str, dict]] = []
    last_flush = time.monotonic()

    async for delta in hctx.llm.stream(messages, tools=hctx.tools.schemas()):
        if delta.result is not None:
            # The assembled message must never be logged before the fragments
            # it was built from, so drain first.
            for event in hctx.emit_many(buffer):
                yield event
            yield delta.result
            return

        # Raw fragments are logged so a replay reproduces the stream exactly.
        buffer.append((ev.ASSISTANT_CHUNK, {
            "delta": delta.content,
            "reasoning": delta.reasoning,
        }))
        now = time.monotonic()
        if len(buffer) >= CHUNK_FLUSH_SIZE or now - last_flush >= CHUNK_FLUSH_SECONDS:
            for event in hctx.emit_many(buffer):
                yield event
            buffer.clear()
            last_flush = now

        if interrupt.is_set(hctx.session_id):
            for event in hctx.emit_many(buffer):
                yield event
            yield hctx.emit(ev.AGENT_INTERRUPT, {"where": "stream"})
            return


async def _run_pending_calls(hctx) -> AsyncIterator[SessionEvent]:
    """Execute every tool call from the latest assistant message still lacking a result."""
    while True:
        log = hctx.store.read(hctx.session_id)
        pending = _pending_calls(log)
        if not pending:
            return

        call = pending[0]
        if interrupt.is_set(hctx.session_id):
            yield hctx.emit(ev.TOOL_RESULT, {
                "tool_call_id": call.get("id", ""),
                "name": call.get("function", {}).get("name", ""),
                "content": INTERRUPTED_RESULT,
                "is_error": True,
            })
            yield hctx.emit(ev.AGENT_INTERRUPT, {"where": "tool"})
            return

        # Every path through _run_call settles the call with either a result
        # or an approval request, so the `pending` list always shrinks.
        async for event in _run_call(hctx, call):
            yield event
            if event.type == ev.TOOL_APPROVAL:
                return                      # stop here; a human has to answer


async def _run_call(hctx, call: dict) -> AsyncIterator[SessionEvent]:
    """Guard, then run, one tool call."""
    call_id = call.get("id", "")
    fn = call.get("function", {})
    name = fn.get("name", "")
    raw_args = fn.get("arguments", "") or "{}"

    yield hctx.emit(ev.TOOL_CALL, {"tool_call_id": call_id, "name": name, "arguments": raw_args})

    if name not in hctx.tools:
        yield hctx.emit(ev.TOOL_RESULT, {
            "tool_call_id": call_id,
            "name": name,
            "content": f"错误：本次会话没有启用工具 {name}",
            "is_error": True,
        })
        return

    spec = hctx.tools.get(name)
    decision = hctx.hooks.emit(PRE_EXECUTE, spec, _safe_args(raw_args))

    if decision is not None and decision.verdict == DENY:
        yield hctx.emit(ev.TOOL_RESULT, {
            "tool_call_id": call_id,
            "name": name,
            "content": f"错误：{decision.reason}",
            "is_error": True,
        })
        return

    if decision is not None and decision.verdict == ASK:
        yield hctx.emit(ev.TOOL_APPROVAL, {
            "tool_call_id": call_id,
            "name": name,
            "arguments": raw_args,
            "reason": decision.reason,
            "permission": spec.permission,
        })
        return

    async for event in _execute(hctx, call, spec):
        yield event


async def _execute(hctx, call: dict, spec) -> AsyncIterator[SessionEvent]:
    """Run a call that has already cleared the guard."""
    call_id = call.get("id", "")
    raw_args = call.get("function", {}).get("arguments", "") or "{}"

    content, is_error = await hctx.tools.execute(spec.name, raw_args, hctx.tool_context)
    yield hctx.emit(ev.TOOL_RESULT, {
        "tool_call_id": call_id,
        "name": spec.name,
        "content": content,
        "is_error": is_error,
        "stops_turn": spec.stops_turn and not is_error,
    })


def _snapshot_prompt(hctx, log: list[SessionEvent]) -> SessionEvent | None:
    """Log the current system prompt if the log does not already end with it."""
    if logged_system_prompt(log) == hctx.system_prompt:
        return None
    return hctx.emit(ev.CONFIG_CHANGE, {"system_prompt": hctx.system_prompt})


def _pending_calls(log: list[SessionEvent]) -> list[dict]:
    """Tool calls from the last assistant message that have no result yet."""
    calls: list[dict] = []
    for event in reversed(log):
        if event.type == ev.ASSISTANT_MESSAGE:
            calls = event.data.get("tool_calls") or []
            break

    settled = {
        e.data.get("tool_call_id")
        for e in log
        if e.type in (ev.TOOL_RESULT, ev.TOOL_APPROVAL)
    }
    return [c for c in calls if c.get("id") not in settled]


def _find_call(log: list[SessionEvent], call_id: str) -> dict | None:
    for event in reversed(log):
        if event.type != ev.ASSISTANT_MESSAGE:
            continue
        for call in event.data.get("tool_calls") or []:
            if call.get("id") == call_id:
                return call
    return None


def _safe_args(raw: str) -> dict:
    """Arguments for the guard's eyes. Malformed JSON is the executor's problem."""
    from harness.tools.registry import parse_arguments
    try:
        return parse_arguments(raw)
    except AppError:
        return {}
