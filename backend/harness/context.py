"""Assembly point.

Every seam in the subsystem is chosen here and nowhere else: the store, the
model adapter, the sandbox, the tool registry, the guard. Swapping any one of
them is a single-line edit in `build_context`, which is the whole reason they
are protocols rather than imports scattered through the loop.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.config import get_settings
from harness import agents
from harness.events import SessionEvent
from harness.llm.registry import build_adapter
from harness.loop.hooks import PRE_EXECUTE, HookBus
from harness.sandbox.local import LocalSandbox
from harness.skills import catalogue as skills_catalogue
from harness.sandbox.workspace import Workspace
from harness.session.sqlite_store import SqliteSessionStore
from harness.tools.approval import ApprovalPolicy, StrictApprovalPolicy
from harness.tools.base import ToolContext
from harness.tools.registry import ToolRegistry

settings = get_settings()


@dataclass
class HarnessContext:
    """One session's fully wired runtime."""

    session_id: str
    store: Any                      # harness.session.store.SessionStore
    llm: Any                        # harness.llm.base.ModelAdapter
    tools: ToolRegistry
    workspace: Workspace
    sandbox: Any                    # harness.sandbox.base.Sandbox
    hooks: HookBus
    system_prompt: str
    max_steps: int
    context_budget: int
    depth: int = 0                  # 0 for a user's session, 1 inside a subagent
    tool_context: ToolContext = field(init=False)

    def __post_init__(self):
        self.tool_context = ToolContext(
            session_id=self.session_id,
            sandbox=self.sandbox,
            workspace=self.workspace,
            skills=self.tools.allowed_skills,
            emit=self.emit,
            depth=self.depth,
        )

    def emit(self, type: str, data: dict) -> SessionEvent:
        """Record an event and hand it back for streaming.

        The single writer for the whole loop: persistence happens here, before
        anyone downstream sees the event.
        """
        return self.store.append(self.session_id, type, data)

    def emit_many(self, entries: list[tuple[str, dict]]) -> list[SessionEvent]:
        """Record a burst of events in one transaction, same ordering guarantee."""
        return self.store.append_many(self.session_id, entries)


def build_hooks(strict: bool = False) -> HookBus:
    """The listeners a session runs with.

    The approval policy is registered as an ordinary listener rather than being
    called from the loop, so a stricter guard, an audit sink or a per-user
    quota is an extra line here instead of an edit to the loop. `strict` picks
    the subagent variant, where nothing can stop to ask a human.
    """
    hooks = HookBus()
    policy = StrictApprovalPolicy() if strict else ApprovalPolicy()
    hooks.on(PRE_EXECUTE, "approval-policy", policy.decide)
    return hooks


def runtime_context() -> str:
    """Facts the model cannot look up, appended to the system prompt.

    Date only, and deliberately so: the provider caches on the prompt prefix,
    and a timestamp that changed every second would invalidate that cache on
    every single request. Daily granularity keeps the cache useful.

    Without this the model spends tool calls working out what day it is — a
    probe run burned three `web_fetch` round trips on it before this existed.
    """
    now = datetime.now().astimezone()
    return (
        "\n\n## 运行环境\n\n"
        f"- 当前日期：{now:%Y-%m-%d}（{now:%A}，时区 {now:%Z} UTC{now:%z}）\n"
        "- 需要精确到时分秒时调用 `current_time` 工具，**不要联网去查时间**\n"
        "- 工作区就是你的当前目录，所有路径相对它，不要尝试访问外部路径\n"
    )


def compose_prompt(registry: ToolRegistry) -> str:
    """The system prompt as one string, in cache-friendly order.

    The skill catalogue changes only when a skill file does; the runtime block
    changes daily. Putting the stable part first keeps the shared prefix as long
    as possible, which is what the provider's prefix cache charges by.
    """
    # The agent catalogue is only worth its tokens when the session can act on
    # it. A registry without `subagent` would be reading about roles it cannot
    # reach — which is exactly the case inside a subagent.
    delegation = agents.catalogue() if "subagent" in registry else ""
    return (
        registry.system_prompt()
        + skills_catalogue(registry.allowed_skills)
        + delegation
        + runtime_context()
    )


def build_context(session_id: str, preset: str = "", model: str = "") -> HarnessContext:
    workspace = Workspace(session_id)
    workspace.ensure()

    registry = ToolRegistry(preset)

    return HarnessContext(
        session_id=session_id,
        store=SqliteSessionStore(),
        llm=build_adapter(model),
        tools=registry,
        workspace=workspace,
        sandbox=LocalSandbox(workspace),
        hooks=build_hooks(),
        system_prompt=compose_prompt(registry),
        max_steps=registry.max_steps,
        context_budget=settings.harness_context_budget_tokens,
    )


def build_subagent_context(
    child_session_id: str, agent_name: str, workspace: Workspace, sandbox, depth: int
) -> HarnessContext:
    """A nested runtime for one delegated task.

    Takes the parent's workspace and sandbox rather than a reference to the
    parent context: a child needs to act on the same files, and passing the two
    objects it actually uses avoids handing every tool handler a way back into
    the whole parent runtime. The store and the adapter are stateless per
    session, so they are simply built again.
    """
    definition = agents.load_agent(agent_name)
    registry = ToolRegistry(definition=definition)

    return HarnessContext(
        session_id=child_session_id,
        store=SqliteSessionStore(),
        llm=build_adapter(definition.get("model", "")),
        tools=registry,
        workspace=workspace,
        sandbox=sandbox,
        hooks=build_hooks(strict=True),
        system_prompt=compose_prompt(registry),
        max_steps=min(registry.max_steps, settings.harness_subagent_max_steps),
        context_budget=settings.harness_context_budget_tokens,
        depth=depth + 1,
    )
