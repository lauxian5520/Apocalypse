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
from harness.events import SessionEvent
from harness.llm.registry import build_adapter
from harness.loop.hooks import PRE_EXECUTE, HookBus
from harness.sandbox.local import LocalSandbox
from harness.sandbox.workspace import Workspace
from harness.session.sqlite_store import SqliteSessionStore
from harness.tools.approval import ApprovalPolicy
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
    tool_context: ToolContext = field(init=False)

    def __post_init__(self):
        self.tool_context = ToolContext(
            session_id=self.session_id, sandbox=self.sandbox, workspace=self.workspace
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


def build_hooks() -> HookBus:
    """The listeners every session runs with.

    The approval policy is registered as an ordinary listener rather than being
    called from the loop, so a stricter guard, an audit sink or a per-user
    quota is an extra line here instead of an edit to the loop.
    """
    hooks = HookBus()
    hooks.on(PRE_EXECUTE, "approval-policy", ApprovalPolicy().decide)
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
        "- 工作区就是你的当前目录，所有路径相对它，不要尝试访问外部路径\n"
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
        system_prompt=registry.system_prompt() + runtime_context(),
        max_steps=registry.max_steps,
        context_budget=settings.harness_context_budget_tokens,
    )
