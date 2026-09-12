"""Running many episodes at once, with continuous refill.

The design point is the scheduling discipline, because rollout dominates the
wall clock of an RL loop and the naive version wastes most of it.

**Batch-synchronous** — start N episodes, wait for all N, start the next N — is
what a `asyncio.gather` over chunks gives you, and it is bounded by the *slowest*
episode in each batch. Trajectory length varies a lot here (a question solved in
3 steps versus one that burns all 12), so the tail dominates: for most of each
batch's life only a handful of episodes are still running and the rest of the
concurrency budget sits idle.

**Continuous refill** keeps exactly `concurrency` episodes in flight at all
times: the instant one finishes, the next task starts. It is a semaphore and a
task-per-episode, about thirty lines, and it turns the tail into overlap. The
engine records both so the difference can be measured rather than asserted —
`--schedule batch` exists purely to produce the comparison number.

One episode never raises into the run. A provider outage kills one trajectory,
which is recorded with its error and counted; it does not abort the other
several hundred.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from harness.loop.agent import run_turn
from harness.session.projection import derive_messages  # noqa: F401  (re-exported for callers)

from rl.env.build import assert_no_compaction, build_env_context
from rl.env.memory_store import MemorySessionStore, new_session_id
from rl.rollout.trajectory import EnvStamp, Trajectory
from rl.verifiers import reward as reward_verifier
from rl.verifiers import trace as trace_extractor

logger = logging.getLogger(__name__)

# How a task becomes the model's first message. Kept here rather than in the
# system prompt because it is per-episode, and kept minimal because anything
# added is a hint the policy did not have to earn.
TASK_TEMPLATE = "{question}"


@dataclass
class RunStats:
    """Wall-clock accounting for one run."""

    started: float = 0.0
    finished: float = 0.0
    episode_seconds: list[float] = field(default_factory=list)
    generation_seconds: float = 0.0
    concurrency: int = 0
    schedule: str = "refill"

    @property
    def wall_seconds(self) -> float:
        return max(self.finished - self.started, 1e-9)

    @property
    def throughput_per_min(self) -> float:
        return 60.0 * len(self.episode_seconds) / self.wall_seconds

    def percentile(self, p: float) -> float:
        if not self.episode_seconds:
            return 0.0
        ordered = sorted(self.episode_seconds)
        idx = min(int(p / 100.0 * len(ordered)), len(ordered) - 1)
        return ordered[idx]

    def to_dict(self) -> dict:
        total = sum(self.episode_seconds)
        slowest = sorted(self.episode_seconds, reverse=True)[: max(1, len(self.episode_seconds) // 20)]
        return {
            "episodes": len(self.episode_seconds),
            "schedule": self.schedule,
            "concurrency": self.concurrency,
            "wall_seconds": round(self.wall_seconds, 2),
            "throughput_per_min": round(self.throughput_per_min, 2),
            "p50_seconds": round(self.percentile(50), 2),
            "p95_seconds": round(self.percentile(95), 2),
            "p99_seconds": round(self.percentile(99), 2),
            # What fraction of all episode time the slowest 5% account for —
            # the long-tail number the scheduling choice is meant to move.
            "slowest_5pct_share": round(sum(slowest) / total, 4) if total else 0.0,
            # Occupancy: how much of the available concurrency was actually busy.
            "utilisation": round(total / (self.wall_seconds * max(self.concurrency, 1)), 4),
        }


def shared(adapter):
    """Wrap one adapter instance as a policy factory.

    Evaluation wants a single adapter for the whole run; training wants one per
    episode. Rather than sniff which was passed — an adapter *class* has a
    `stream` attribute just like an instance does, so duck-typing picks the
    wrong branch — the engine always takes a factory and this makes the
    shared case explicit at the call site.
    """
    return lambda: adapter


async def run_episode(task: dict, policy, stamp: EnvStamp, weights=None) -> Trajectory:
    """One task, start to finish. Never raises.

    `policy` is a zero-argument factory returning a `ModelAdapter`; use
    `shared(adapter)` to reuse one instance. A fresh adapter per episode is what
    training needs: the policy adapter accumulates the per-generation records the
    trainer consumes (`logp_old` among them), and with one adapter across
    concurrent episodes those records interleave with no way to tell which
    trajectory each belongs to.
    """
    started = time.monotonic()
    llm = policy()
    # A shared adapter's counter is cumulative across every episode, so the
    # per-episode cost is the delta, not the reading.
    generation_before = float(getattr(llm, "generation_seconds", 0.0) or 0.0)
    store = MemorySessionStore()
    session_id = new_session_id()

    trajectory = Trajectory(
        task_id=task["task_id"],
        question=task["question"],
        gold_answer=task["answer"],
        stamp=stamp.to_dict(),
    )

    try:
        hctx = build_env_context(session_id=session_id, store=store, llm=llm)
        async for _ in run_turn(hctx, TASK_TEMPLATE.format(question=task["question"])):
            pass                      # events are already in the store

        log = store.read(session_id)
        # Compaction would have rewritten the system message mid-episode, which
        # breaks the token-prefix property the trainer relies on. Fail the
        # trajectory rather than quietly training on it.
        assert_no_compaction(log)

        trace = trace_extractor.extract(log)
        reward = reward_verifier.score(trace, task, weights)
        trajectory.events = [e.to_dict() for e in log]
        trajectory.reward = reward.to_dict()
        # Present only on the training adapter; evaluation does not record them.
        recorded = getattr(llm, "steps", None)
        if recorded:
            trajectory.sampled = [s.to_dict() for s in recorded]
    except Exception as e:                      # noqa: BLE001 - one episode must not kill a run
        logger.warning("episode %s failed: %s: %s", task.get("task_id"), type(e).__name__, e)
        trajectory.error = f"{type(e).__name__}: {e}"
        trajectory.events = [e.to_dict() for e in store.read(session_id)]
    finally:
        store.drop(session_id)
        trajectory.seconds = time.monotonic() - started
        trajectory.generation_seconds = (
            float(getattr(llm, "generation_seconds", 0.0) or 0.0) - generation_before
        )

    return trajectory


async def run_many(
    tasks: list[dict],
    policy,
    stamp: EnvStamp,
    *,
    concurrency: int = 8,
    weights=None,
    schedule: str = "refill",
    progress_every: int = 25,
    group_size: int = 1,
) -> tuple[list[Trajectory], RunStats]:
    """Roll out every task. `schedule` is "refill" or "batch".

    `group_size` is G in GRPO: each task is rolled out G times, and the group's
    mean reward becomes the baseline for every member. G must be > 1 for
    training — with G = 1 the group mean *is* the sample, so every advantage is
    identically zero and no gradient exists. G = 1 is right for evaluation,
    where each question is answered once.

    The G copies of a task are deliberately adjacent in the task list: a
    provider's prefix cache is keyed on the shared prompt, and they share all of
    it up to the first sampled token.
    """
    if group_size > 1:
        tasks = [task for task in tasks for _ in range(group_size)]

    stats = RunStats(concurrency=concurrency, schedule=schedule)
    stats.started = time.monotonic()
    results: list[Trajectory] = []

    if schedule == "batch":
        # Deliberately the slower discipline, kept so the comparison can be
        # measured rather than claimed. Each chunk waits for its slowest member.
        for i in range(0, len(tasks), concurrency):
            chunk = tasks[i:i + concurrency]
            done = await asyncio.gather(*(run_episode(t, policy, stamp, weights) for t in chunk))
            results.extend(done)
            _log_progress(len(results), len(tasks), progress_every)
    else:
        semaphore = asyncio.Semaphore(concurrency)

        async def guarded(task: dict) -> Trajectory:
            # The semaphore is what makes this "refill": a slot is released the
            # moment an episode ends, and the next queued episode takes it
            # immediately rather than waiting for its cohort.
            async with semaphore:
                return await run_episode(task, policy, stamp, weights)

        pending = [asyncio.create_task(guarded(t)) for t in tasks]
        for coro in asyncio.as_completed(pending):
            results.append(await coro)
            _log_progress(len(results), len(tasks), progress_every)

    stats.finished = time.monotonic()
    stats.episode_seconds = [t.seconds for t in results]
    # Summed from the episodes rather than read off an adapter: the factory
    # path has no single object to read, and calling it again just to look
    # would construct a spurious adapter.
    stats.generation_seconds = sum(t.generation_seconds for t in results)
    return results, stats


def _log_progress(done: int, total: int, every: int) -> None:
    if every and done % every == 0:
        logger.info("  %d/%d episodes", done, total)
