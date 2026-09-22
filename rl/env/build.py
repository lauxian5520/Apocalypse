"""Assemble a `HarnessContext` for one rollout.

`harness/context.py::build_context()` is the website's assembly point. This is
the RL one. It deliberately does *not* call it, and every difference is a
requirement of training rather than a preference:

- **The system prompt is pinned, and excludes the runtime block.**
  `compose_prompt()` appends `runtime_context()`, which carries *today's date*
  (`harness/context.py:82-98`). That is right for a chat session and fatal for a
  dataset: a trajectory collected on Tuesday and replayed on Wednesday would
  have a different system message, so its recorded tokens would no longer match
  a re-tokenization, and the prefix property the loss mask depends on breaks.
  The prompt here is a pure function of `deepresearch.md`.
- **Compaction is switched off** by making the budget unreachable.
  `maybe_compact()` runs on *every* step (`harness/loop/agent.py:152`) and, when
  it fires, rewrites the system message mid-trajectory — which also breaks the
  prefix property, and silently. `assert_no_compaction()` checks the log rather
  than trusting the budget.
- **The store is in-memory** (see `memory_store.py`).
- **One workspace is shared by every rollout**, not one each. The corpus tools
  never touch it; creating a directory per episode would leave a hundred
  thousand empty ones behind. It exists only so a mis-specified preset
  containing a file tool fails with a readable message instead of an
  `AttributeError` on `None`.

Everything else — the loop, the projection, the event vocabulary — is the
harness's, unmodified. That is the point.
"""
import hashlib
import logging

from core.config import get_settings
from harness import events as ev
from harness.context import HarnessContext, build_hooks
from harness.events import SessionEvent
from harness.sandbox.workspace import Workspace
from harness.tools.registry import ToolRegistry

from rl.env.memory_store import MemorySessionStore, new_session_id

logger = logging.getLogger(__name__)
settings = get_settings()

PRESET = "deepresearch"

# The environment's whole action space. Asserted at build time so a preset that
# drifted is caught before a run, not halfway through one.
ENV_TOOLS = ("corpus_search", "corpus_open", "corpus_answer")

# Large enough that `maybe_compact` never triggers. The environment caps steps
# and tool output, so a trajectory cannot approach this; if one ever did, that
# is a bug worth crashing on rather than compacting around.
NO_COMPACTION_BUDGET = 1 << 30

# One directory for every rollout in the process. Named, not random, so it is
# obvious in `var/` what created it.
SHARED_WORKSPACE_ID = "__rl_rollout__"

_shared_workspace: Workspace | None = None


def shared_workspace() -> Workspace:
    global _shared_workspace
    if _shared_workspace is None:
        ws = Workspace(SHARED_WORKSPACE_ID)
        ws.ensure()
        _shared_workspace = ws
    return _shared_workspace


def pinned_system_prompt(registry: ToolRegistry) -> str:
    """The preset's prompt and nothing else — no date, no skills, no agents.

    Deliberately not `compose_prompt()`. See the module docstring: a prompt that
    changes with the calendar cannot be part of a frozen dataset.
    """
    return registry.system_prompt()


def prompt_sha256(registry: ToolRegistry) -> str:
    """Fingerprint of the exact prompt a rollout will send, for `EnvStamp`."""
    return hashlib.sha256(pinned_system_prompt(registry).encode("utf-8")).hexdigest()


def build_env_context(
    *,
    session_id: str = "",
    store: MemorySessionStore | None = None,
    llm=None,
    max_steps: int = 0,
) -> HarnessContext:
    """One episode's runtime.

    `llm` is the policy under evaluation — any object satisfying
    `harness/llm/base.py::ModelAdapter`. It is required rather than defaulted so
    a rollout can never silently fall back to the website's configured provider
    and bill a training run to the chat key.
    """
    if llm is None:
        raise ValueError("build_env_context 需要一个 ModelAdapter 作为策略，不接受默认 provider")
    if not settings.harness_corpus_enabled:
        raise RuntimeError(
            "HARNESS_CORPUS_ENABLED=false，corpus 工具被模块门禁挡住了。"
            "运行 rollout 前设置 HARNESS_CORPUS_ENABLED=true。"
        )

    registry = ToolRegistry(PRESET)
    # `ToolRegistry` implements `__contains__` but not `__iter__`, so membership
    # has to be asked one name at a time.
    missing = sorted(n for n in ENV_TOOLS if n not in registry)
    if missing:
        raise RuntimeError(f"deepresearch 预设缺少工具 {missing}——环境定义不完整")

    return HarnessContext(
        session_id=session_id or new_session_id(),
        store=store if store is not None else MemorySessionStore(),
        llm=llm,
        tools=registry,
        workspace=shared_workspace(),
        sandbox=None,                       # no tool in this preset executes anything
        hooks=build_hooks(),                # every corpus tool is `read`, so nothing asks
        system_prompt=pinned_system_prompt(registry),
        max_steps=max_steps or registry.max_steps,
        context_budget=NO_COMPACTION_BUDGET,
    )


def assert_no_compaction(log: list[SessionEvent]) -> None:
    """Fail loudly if compaction touched a trajectory destined for training.

    A compacted log still projects into valid messages, so nothing downstream
    would notice — it would just train on tokens the policy never emitted.
    """
    for event in log:
        if event.type == ev.COMPACTION_SUMMARY:
            raise RuntimeError(
                f"轨迹中出现 compaction/summary（seq {event.seq}）——"
                "系统消息被中途改写，前缀性质已破坏，这条轨迹不能用于训练"
            )


def assert_single_system_prompt(log: list[SessionEvent]) -> str:
    """Exactly one system-prompt snapshot, and return it.

    The loop re-snapshots whenever the prompt differs from the last one logged
    (`agent.py::_snapshot_prompt`). With the runtime block excluded that should
    happen exactly once per session; more than once means something is
    recomputing the prompt, which is the date bug coming back.
    """
    snapshots = [e for e in log if e.type == ev.CONFIG_CHANGE and "system_prompt" in e.data]
    if len(snapshots) != 1:
        raise RuntimeError(
            f"轨迹里有 {len(snapshots)} 个系统提示词快照，应当恰好 1 个——"
            "提示词在会话中途变了，数据集不可复现"
        )
    return snapshots[0].data["system_prompt"]


def corpus_dir() -> str:
    return settings.harness_corpus_dir


def describe() -> str:
    """One line pinning what this environment currently is, for run records."""
    from harness.corpus.store import load_manifest

    try:
        manifest = load_manifest(corpus_dir())
        corpus = f"{manifest.doc_count} docs @ {manifest.short_hash}"
    except (FileNotFoundError, OSError):
        corpus = "corpus MISSING"
    registry = ToolRegistry(PRESET)
    return f"preset={PRESET} tools={len(registry.schemas())} max_steps={registry.max_steps} corpus={corpus}"


__all__ = [
    "build_env_context",
    "assert_no_compaction",
    "assert_single_system_prompt",
    "pinned_system_prompt",
    "shared_workspace",
    "corpus_dir",
    "describe",
    "PRESET",
]
