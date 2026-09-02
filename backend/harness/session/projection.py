"""The one place a session log turns into model messages.

`derive_messages()` is the only function in the subsystem allowed to build a
request body's `messages`. Keeping it alone here is what makes the promise
"model-visible means logged" checkable: if it is not in the log, it cannot be
in the request.
"""
from harness import events as ev
from harness.events import SessionEvent

# Shown in place of a tool result that never arrived — an interrupted or
# abandoned call. The provider rejects an assistant message whose tool_calls
# have no matching `tool` reply, so a placeholder is mandatory, not cosmetic.
ORPHAN_RESULT = "[未完成：该工具调用被中断或取消]"

COMPACTION_HEADER = "以下是本次会话早期内容的摘要（原始事件仍保留在日志中）：\n"


def logged_system_prompt(log: list[SessionEvent]) -> str | None:
    """The newest system-prompt snapshot in the log, or None if it has none.

    None means a session recorded before snapshots existed; the caller's own
    prompt is the only thing left to fall back on.
    """
    for e in reversed(log):
        if e.type == ev.CONFIG_CHANGE and "system_prompt" in e.data:
            return e.data["system_prompt"]
    return None


def derive_messages(log: list[SessionEvent], system_prompt: str) -> list[dict]:
    """Fold an event log into the exact `messages` array sent to the provider.

    The system prompt comes from the log when one was recorded there. It has to:
    the prompt is rebuilt from `system.md` plus a runtime block containing
    today's date, so recomputing it would replay an old session with a date the
    model never saw — and with whatever `system.md` says now rather than what it
    said then. `system_prompt` is the fallback for logs predating snapshots.
    """
    summary, covered_to = _latest_compaction(log)

    system = logged_system_prompt(log) or system_prompt
    if summary:
        system = f"{system}\n\n{COMPACTION_HEADER}{summary}"
    messages: list[dict] = [{"role": "system", "content": system}]

    results = _results_by_call_id(log)

    for e in log:
        if e.seq <= covered_to:
            continue

        if e.type == ev.USER_MESSAGE:
            messages.append({"role": "user", "content": e.data.get("content", "")})

        elif e.type == ev.ASSISTANT_MESSAGE:
            msg: dict = {"role": "assistant", "content": e.data.get("content", "") or ""}
            calls = e.data.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = calls
            messages.append(msg)
            # Every tool reply must directly follow the call that asked for it,
            # so results are placed from here rather than from their own event.
            # A tool/result whose call was compacted away is dropped with it.
            for call in calls:
                call_id = call.get("id", "")
                messages.append(results.get(call_id) or _orphan(call_id))

    return messages


def _orphan(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": ORPHAN_RESULT}


def _results_by_call_id(log: list[SessionEvent]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in log:
        if e.type != ev.TOOL_RESULT:
            continue
        call_id = e.data.get("tool_call_id", "")
        if call_id:
            out[call_id] = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": e.data.get("content", ""),
            }
    return out


def _latest_compaction(log: list[SessionEvent]) -> tuple[str, int]:
    """The newest summary and the seq it covers up to (-1 when uncompacted)."""
    summary, covered_to = "", -1
    for e in log:
        if e.type == ev.COMPACTION_SUMMARY:
            summary = e.data.get("summary", "")
            covered_to = int(e.data.get("covers_to_seq", -1))
    return summary, covered_to


def compaction_boundary(log: list[SessionEvent], keep_recent_turns: int = 2) -> int:
    """Highest seq that is safe to compact away, or -1 when there is nothing.

    The boundary always sits immediately before a `user/message`. That is the
    only cut guaranteed not to orphan a tool call from its results.
    """
    user_seqs = [e.seq for e in log if e.type == ev.USER_MESSAGE]
    if len(user_seqs) <= keep_recent_turns:
        return -1
    return user_seqs[-keep_recent_turns] - 1


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token count for budget decisions.

    Deliberately an estimate: a real count costs a provider round-trip, and
    this only needs to decide *whether* to compact. CJK text runs about one
    token per character, English about four characters per token; ~2.5 splits
    the difference without over-thinking a heuristic.
    """
    chars = 0
    for m in messages:
        chars += len(str(m.get("content") or ""))
        for call in m.get("tool_calls") or []:
            chars += len(str(call.get("function", {}).get("arguments", "")))
    return int(chars / 2.5)
