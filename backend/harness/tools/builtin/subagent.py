"""Delegation: run a nested agent turn and bring back only its conclusion.

The child gets its own session id and therefore its own complete event log, so
nothing is hidden — the parent's log records the delegation, the child's records
every step of it. What the *parent model* sees is just this tool's result, which
is the entire point: a task that takes twenty steps and produces three sentences
should cost the parent three sentences of context.

Every limit in here is a spend limit. A delegated run is the one place in the
subsystem where the agent can start work that nobody is watching turn by turn.
"""
import logging
import time

from core.config import get_settings
from core.errors import ValidationError
from harness import agents
from harness import events as ev
from harness.context import build_subagent_context
from harness.loop import interrupt
from harness.loop.agent import run_turn
from harness.session import manager
from harness.tools.base import ToolContext

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_TASK_CHARS = 4000
MAX_CONTEXT_CHARS = 8000


async def subagent(ctx: ToolContext, task: str, agent: str = "", context: str = "") -> str:
    if not settings.harness_subagent_enabled:
        raise ValidationError("本部署没有启用子代理")

    # Depth first: it is the guard that stops a runaway from multiplying, and it
    # costs nothing to check.
    if ctx.depth >= settings.harness_subagent_max_depth:
        raise ValidationError("子代理不能再派发子代理，请自己完成这一步")

    task = (task or "").strip()
    if not task:
        raise ValidationError("task 不能为空，子代理看不到你和用户的对话")
    if len(task) > MAX_TASK_CHARS:
        raise ValidationError(f"task 过长，请压缩到 {MAX_TASK_CHARS} 字以内")

    agent = (agent or "").strip()
    known = agents.names()
    if agent not in known:
        raise ValidationError(f"没有名为 {agent!r} 的子代理。可用：{'、'.join(known) or '（无）'}")

    used = manager.count_children(ctx.session_id)
    limit = settings.harness_subagent_max_per_session
    if used >= limit:
        raise ValidationError(f"本会话已派发 {used} 个子代理，达到上限 {limit}，请自己完成剩下的部分")

    child = manager.create_subagent(ctx.session_id, agent, task)
    child_ctx = build_subagent_context(
        child.id, agent, ctx.workspace, ctx.sandbox, ctx.depth
    )
    _emit(ctx, ev.SUBAGENT_START, {
        "child_session_id": child.id, "agent": agent, "task": task,
    })

    outcome = await _run(ctx, child_ctx, _brief(task, context))
    usage = manager.usage_summary(child.id)

    _emit(ctx, ev.SUBAGENT_END, {
        "child_session_id": child.id,
        "agent": agent,
        "steps": outcome["steps"],
        "stopped": outcome["stopped"],
        "usage": usage,
    })

    return _report(agent, child.id, outcome, usage)


async def _run(ctx: ToolContext, child_ctx, prompt: str) -> dict:
    """Drive the child turn, watching the two things that must stop it."""
    deadline = time.monotonic() + settings.harness_subagent_timeout_seconds
    answer, stopped, steps = "", "", 0

    manager.set_status(child_ctx.session_id, "running")
    try:
        async for event in run_turn(child_ctx, prompt):
            if event.type == ev.STEP_START:
                steps += 1
            elif event.type == ev.ASSISTANT_MESSAGE:
                # The child's last piece of prose is its deliverable. Keeping the
                # newest non-empty one means a final summary wins over the
                # narration that came before the tool calls.
                content = (event.data.get("content") or "").strip()
                if content:
                    answer = content
            elif event.type == ev.AGENT_ERROR:
                stopped = stopped or event.data.get("message", "子代理报错")

            # A user interrupting the parent means the whole tree stops. The
            # parent's own loop cannot see this call, which is already running.
            if interrupt.is_set(ctx.session_id):
                stopped = stopped or "父会话被中断"
                interrupt.request(child_ctx.session_id)
            elif time.monotonic() > deadline:
                stopped = stopped or f"超过 {settings.harness_subagent_timeout_seconds} 秒时限"
                interrupt.request(child_ctx.session_id)
    except Exception as e:
        # Never let a child take the parent's turn down; the failure is a value
        # the parent model can read and route around.
        logger.exception("[harness] subagent %s failed", child_ctx.session_id)
        stopped = stopped or f"{e.__class__.__name__}: {e}"
    finally:
        manager.finalize_turn(child_ctx.session_id)

    return {"answer": answer, "stopped": stopped, "steps": steps}


def _brief(task: str, context: str) -> str:
    context = (context or "").strip()[:MAX_CONTEXT_CHARS]
    if not context:
        return task
    return f"{task}\n\n## 上级提供的背景\n\n{context}"


def _report(agent: str, child_id: str, outcome: dict, usage: dict) -> str:
    """What the parent model reads. Honest about a run that did not finish."""
    if outcome["answer"]:
        body = outcome["answer"]
        if outcome["stopped"]:
            body += f"\n\n（注意：这轮没有正常结束——{outcome['stopped']}，结论可能不完整）"
    else:
        body = f"子代理没有给出结论：{outcome['stopped'] or '它没有输出任何文本'}"

    return (
        f"{body}\n\n"
        f"[子代理 {agent} · 会话 {child_id[:8]} · {outcome['steps']} 步 · "
        f"{usage.get('total_tokens', 0)} tokens]"
    )


def _emit(ctx: ToolContext, event_type: str, data: dict) -> None:
    """Record the delegation in the parent's trajectory. Best effort."""
    if ctx.emit is None:
        return
    try:
        ctx.emit(event_type, data)
    except Exception:
        logger.exception("[harness] could not record %s", event_type)


HANDLERS = {"subagent": subagent}
