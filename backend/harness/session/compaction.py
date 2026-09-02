"""Context compaction.

When a session outgrows its budget, the oldest stretch is replaced by a summary
so the run can continue. The original events are never removed — the log stays
append-only, and `derive_messages()` simply stops projecting the covered range.
"""
import logging
from pathlib import Path

from harness import events as ev
from harness.events import SessionEvent
from harness.session.projection import compaction_boundary, derive_messages, estimate_tokens

logger = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).resolve().parent.parent / "data" / "prompts" / "compaction.md"

# Below this, a summary is not worth the request that produces it. It also
# stops a pathological loop: every request carries an irreducible floor (the
# system prompt plus the tool schemas, well over a thousand tokens) that no
# amount of compacting can remove, so a budget set near or below that floor
# would otherwise summarise on every single step and never come under it.
MIN_COMPACTION_GAIN_TOKENS = 500
# Includes headroom for a thinking model's reasoning tokens, which are spent
# before any summary text appears. See TITLE_MAX_TOKENS.
SUMMARY_MAX_TOKENS = 3000


def _measured_prompt_tokens(log: list[SessionEvent]) -> int | None:
    """The provider's own count for the last request, if it is still valid.

    Far better than any heuristic: `estimate_tokens` sees only message text,
    while a real request also carries the system prompt and — much larger — the
    tool schemas, which together ran to thousands of tokens the estimate never
    counted. A session measured at 9,874 prompt tokens estimated at 426.

    Returns None when there is no usable measurement: either none yet, or the
    newest one predates a compaction and so describes a context that no longer
    exists. Compacting on a stale figure would compact again and again without
    ever seeing the effect.
    """
    usage_seq, usage_tokens, compaction_seq = -1, None, -1
    for e in log:
        if e.type == ev.LLM_USAGE:
            usage_seq, usage_tokens = e.seq, e.data.get("prompt_tokens")
        elif e.type == ev.COMPACTION_SUMMARY:
            compaction_seq = e.seq
    if usage_tokens is None or usage_seq < compaction_seq:
        return None
    return int(usage_tokens)


async def maybe_compact(hctx, log: list[SessionEvent], budget_tokens: int) -> list[SessionEvent]:
    """Compact if over budget. Returns the log to project from."""
    used = _measured_prompt_tokens(log)
    if used is None:
        # No measurement to go on (first request of a session, or one already
        # taken before the last compaction) — fall back to the estimate.
        used = estimate_tokens(derive_messages(log, hctx.system_prompt))
    if used <= budget_tokens:
        return log

    boundary = compaction_boundary(log)
    if boundary < 0:
        # Nothing safe to cut: the whole session is one long turn. Better to
        # let the provider complain than to orphan a tool call.
        logger.info("[harness] over budget but no safe compaction boundary")
        return log

    gain = _compactable_tokens(log, hctx.system_prompt, boundary)
    if gain < MIN_COMPACTION_GAIN_TOKENS:
        logger.warning(
            "[harness] %s 超出预算 %s（当前 %s），但可压缩的历史只有约 %s tokens。"
            "请求的固定开销（系统提示词 + 工具 schema）本身就接近预算，"
            "请调高 HARNESS_CONTEXT_BUDGET_TOKENS。",
            hctx.session_id, budget_tokens, used, gain,
        )
        return log

    summary = await _summarize(hctx, log, boundary)
    if not summary:
        return log

    hctx.store.append(hctx.session_id, ev.COMPACTION_SUMMARY, {
        "covers_to_seq": boundary,
        "summary": summary,
    })
    logger.info("[harness] compacted %s up to seq %s (was %s tokens, budget %s)",
                hctx.session_id, boundary, used, budget_tokens)
    return hctx.store.read(hctx.session_id)


async def _summarize(hctx, log: list[SessionEvent], boundary: int) -> str:
    try:
        instruction = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.error("[harness] compaction prompt unreadable: %s", e)
        return ""

    transcript = _render(log, boundary)
    if not transcript:
        return ""

    try:
        result = await hctx.llm.complete(
            [{"role": "user", "content": f"{instruction}\n\n---\n\n{transcript}"}],
            max_tokens=SUMMARY_MAX_TOKENS,
        )
    except Exception as e:
        # Compaction is best-effort: a failed summary must not kill the turn.
        logger.warning("[harness] compaction failed: %s", e)
        return ""
    return result.content.strip()


def _compactable_tokens(log: list[SessionEvent], system_prompt: str, boundary: int) -> int:
    """Roughly how much a compaction at `boundary` would remove.

    The difference between projecting the whole log and projecting only what
    survives the cut — i.e. the part compaction can actually reach, which
    excludes the system prompt and the tool schemas.
    """
    before = estimate_tokens(derive_messages(log, system_prompt))
    after = estimate_tokens(derive_messages([e for e in log if e.seq > boundary], system_prompt))
    return max(0, before - after)


def _render(log: list[SessionEvent], boundary: int) -> str:
    lines = []
    for e in log:
        if e.seq > boundary:
            break
        if e.type == ev.USER_MESSAGE:
            lines.append(f"[用户] {e.data.get('content', '')}")
        elif e.type == ev.ASSISTANT_MESSAGE:
            text = e.data.get("content", "")
            if text:
                lines.append(f"[助手] {text}")
            for call in e.data.get("tool_calls") or []:
                fn = call.get("function", {})
                lines.append(f"[调用] {fn.get('name', '')} {fn.get('arguments', '')[:300]}")
        elif e.type == ev.TOOL_RESULT:
            lines.append(f"[结果] {str(e.data.get('content', ''))[:500]}")
    return "\n".join(lines)
