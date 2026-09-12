"""The GRPO training loop: rollout → score → pack → step → swap.

Every part of this has been built and checked separately. What this module adds
is the order they run in, and the order is where the remaining mistakes live.

One step:

1. Sample questions from the curriculum's **active** pool (§`curriculum.py`).
2. Roll out G trajectories each, against the vLLM server currently serving the
   latest adapter — so the data is on-policy with respect to the weights being
   updated.
3. Score with the verifiers. Feed the outcomes back to the curriculum.
4. Pack into token sequences with loss masks (§`template.pack`).
5. Group-normalise rewards into advantages, dropping degenerate groups.
6. One optimiser step on the clipped token-level loss.
7. Save the LoRA adapter and swap it into the server, so step *k+1* samples
   from the weights step *k* produced.

Step 7 is what keeps the run on-policy, and skipping it is the most expensive
silent failure available here: training proceeds, the loss moves, and every
rollout after step 1 comes from the base model. `lora_serve.check_runtime_lora`
is called once before the loop for exactly that reason.

**The reference model for the KL term is the base model with the adapter
switched off**, via `peft`'s `disable_adapter()`. A separate frozen copy would
double the weights in VRAM for no benefit — LoRA already keeps the base intact,
so the reference is one context manager away.

The torch-dependent work is isolated behind `TrainHooks` so the orchestration —
sampling, curriculum updates, metric accumulation, warnings — is exercised by
the check script with stubs on a machine with no GPU. The parts that genuinely
need a card are the three hook calls, and nothing else.
"""
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from rl.rollout.trajectory import Trajectory
from rl.train import metrics as metrics_mod
from rl.train import pack as packer
from rl.train.curriculum import CurriculumPool

logger = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    steps: int = 500
    questions_per_step: int = 8
    group_size: int = 8                 # G
    batch_size: int = 8
    concurrency: int = 16
    eval_every: int = 25
    save_every: int = 25
    seed: int = 0
    reward_threshold: float = 1.0       # what counts as "solved" for the curriculum


class TrainHooks(Protocol):
    """The three things that need a GPU. Everything else is plain Python."""

    def forward_backward(self, batch: packer.Batch, advantages: list[float],
                         keep: list[bool]) -> dict:
        """One optimiser step over `batch`, weighted by `advantages`.

        `keep[i]` is False for samples in degenerate groups; the hook should
        exclude them rather than multiply by a zero advantage, so they do not
        dilute the token-level denominator.

        Returns metrics (loss, kl, entropy, clip_fraction…).
        """
        ...

    def publish(self, step: int) -> str:
        """Save the adapter and swap it into the rollout server. Returns its name."""
        ...

    def evaluate(self, step: int) -> dict:
        """Score the dev split with the current weights."""
        ...


@dataclass
class LoopState:
    pool: CurriculumPool
    log: metrics_mod.MetricLog
    rng: random.Random
    adapter: str = ""
    eval_history: list[dict] = field(default_factory=list)


def group_rewards(trajectories: list[Trajectory]) -> dict[str, list[float]]:
    """Rewards grouped by question, preserving rollout order within a group."""
    grouped: dict[str, list[float]] = {}
    for trajectory in trajectories:
        grouped.setdefault(trajectory.task_id, []).append(
            float((trajectory.reward or {}).get("total") or 0.0)
        )
    return grouped


def all_zero_fraction(grouped: dict[str, list[float]], threshold: float = 1e-6) -> float:
    """Share of groups whose rewards are all identical — the no-gradient share.

    The single most important health number in a GRPO run and the one a reward
    curve cannot show: these groups contribute exactly zero gradient while
    costing a full G rollouts each.
    """
    if not grouped:
        return 0.0
    degenerate = sum(
        1 for rewards in grouped.values()
        if max(rewards) - min(rewards) < threshold
    )
    return degenerate / len(grouped)


def run(
    tasks: list[dict],
    hooks: TrainHooks,
    rollout: Callable[[list[dict], int], list[Trajectory]],
    config: LoopConfig | None = None,
    metrics_path: str = "",
) -> LoopState:
    """Drive GRPO training.

    `rollout(tasks, group_size) -> trajectories` is injected: during training it
    drives the harness loop against vLLM, and during a check it returns canned
    trajectories. Keeping it a parameter is what makes this function testable
    without a GPU, and it costs nothing at runtime.
    """
    cfg = config or LoopConfig()
    by_id = {task["task_id"]: task for task in tasks}
    state = LoopState(
        pool=CurriculumPool(list(by_id)),
        log=metrics_mod.MetricLog(path=metrics_path),
        rng=random.Random(cfg.seed),
    )

    for step in range(cfg.steps):
        started = time.monotonic()

        chosen_ids = state.pool.sample(cfg.questions_per_step, state.rng)
        chosen = [by_id[task_id] for task_id in chosen_ids]
        trajectories = rollout(chosen, cfg.group_size)
        rollout_seconds = time.monotonic() - started

        usable = [t for t in trajectories if t.ok]
        grouped = group_rewards(usable)
        for task_id, rewards in grouped.items():
            state.pool.observe_group(task_id, rewards, cfg.reward_threshold)

        row = metrics_mod.StepMetrics(
            step=step,
            groups=len(grouped),
            all_zero_groups=all_zero_fraction(grouped),
            curriculum_active=len(state.pool.active_ids()),
            rollout_seconds=round(rollout_seconds, 2),
            adapter=state.adapter,
            **metrics_mod.from_rewards([t.reward for t in usable]),
        )

        batches = packer.build_batches(usable, batch_size=cfg.batch_size)
        train_started = time.monotonic()
        for batch in batches:
            # Advantages are computed here, not in the hook: every group is
            # whole within one batch (`build_batches` guarantees it), so the
            # baseline is the group's real mean rather than whichever part of it
            # happened to land here. Doing it in the loop also keeps the group
            # logic on the testable side of the GPU boundary.
            advantages, keep = packer.group_advantages(
                [s.reward for s in batch.samples], batch.group_ids())
            if not any(keep):
                # Every group in this batch was degenerate. Stepping on it would
                # apply a zero gradient and still pay for the forward pass.
                continue
            stats = hooks.forward_backward(batch, advantages, keep)
            for key, value in (stats or {}).items():
                if hasattr(row, key):
                    setattr(row, key, value)
        row.train_seconds = round(time.monotonic() - train_started, 2)
        row.trainable_tokens = sum(sum(s.mask) for b in batches for s in b.samples)

        state.log.append(row)
        for warning in state.log.warnings(row):
            logger.warning("[step %d] %s", step, warning)

        if batches and (step + 1) % cfg.save_every == 0:
            # Swapping is what keeps the next step on-policy. Without it the
            # run trains one thing and samples another, silently.
            state.adapter = hooks.publish(step + 1)

        if cfg.eval_every and (step + 1) % cfg.eval_every == 0:
            result = hooks.evaluate(step + 1)
            result["step"] = step + 1
            state.eval_history.append(result)
            logger.info("[step %d] dev: %s", step + 1, result)

    return state
