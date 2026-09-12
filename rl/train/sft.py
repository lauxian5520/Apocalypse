"""Rejection-sampling SFT: the mandatory cold start before GRPO.

Not optional, and the reason is arithmetic. GRPO's advantage is the reward
centred on its group's mean. If all G rollouts of a question score 0 — which is
what a 1.5B policy does on a multi-hop retrieval task it has never been shown —
the group's standard deviation is 0, every advantage is exactly 0, and the
gradient is exactly 0. The run burns GPU hours and learns nothing, and the loss
curve looks calm while it happens.

So: sample with a strong model through the **same** `run_turn`, the **same**
tools and the **same** preset, keep only the trajectories the verifier passes,
and fine-tune on those. One verifier serves as both the RL reward and the SFT
filter, which is a property worth having rather than a coincidence — the SFT
target distribution is by construction the one the reward function likes.

**The loss is deliberately the same construction GRPO uses.** Same
`template.pack()`, same mask, same token-level normalisation — the objective
itself is `grpo.masked_cross_entropy`, which lives there precisely so the two
share one shift convention rather than two copies of it. That makes SFT the
canary for the mask: if the spans are off by a token, or the stop token is
outside the mask, SFT either fails to converge or produces a model that
generates garbage in rollout — loud, early, and cheap, on a run that costs a
fraction of an RL run.

Dedup matters more than it sounds. Without it a handful of easy questions with
many successful rollouts dominate the gradient, and the model learns their
phrasing rather than the behaviour.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass

from rl.rollout.trajectory import Trajectory
from rl.train import pack as packer
from rl.verifiers import trace as trace_extractor

logger = logging.getLogger(__name__)

# At most this many successful trajectories per question. Easy questions
# generate many; letting them through unbalanced turns SFT into memorising a
# few questions' surface form.
MAX_PER_QUESTION = 2


@dataclass
class SFTConfig:
    max_per_question: int = MAX_PER_QUESTION
    # Only trajectories scoring at least this much are kept. Default 1.0 means
    # "the answer was exactly right and nothing was flagged as gaming" — the
    # whole point of rejection sampling is that the bar is the verifier's.
    min_reward: float = 1.0
    require_grounded: bool = True


def behaviour_signature(trajectory: Trajectory) -> tuple:
    """What makes two successful trajectories the *same* demonstration.

    The sequence of tool names, not the arguments. Two rollouts that searched
    with different wording but took the same shape teach the same thing; keeping
    both doubles that lesson's weight for no gain.
    """
    trace = trace_extractor.extract(trajectory.session_events())
    return tuple(call.name for call in trace.calls)


def select(
    trajectories: list[Trajectory],
    config: SFTConfig | None = None,
) -> tuple[list[Trajectory], dict]:
    """Filter rollouts down to an SFT set. Returns (kept, funnel)."""
    cfg = config or SFTConfig()

    funnel = {
        "total": len(trajectories),
        "errored": 0,
        "below_reward": 0,
        "ungrounded": 0,
        "duplicate": 0,
        "over_quota": 0,
        "kept": 0,
    }

    by_question: dict[str, list[Trajectory]] = defaultdict(list)
    seen: dict[str, set[tuple]] = defaultdict(set)

    for trajectory in trajectories:
        if not trajectory.ok:
            funnel["errored"] += 1
            continue

        reward = trajectory.reward
        if float(reward.get("total") or 0.0) < cfg.min_reward or not reward.get("correct"):
            funnel["below_reward"] += 1
            continue
        if cfg.require_grounded and not float(reward.get("grounding_f1") or 0.0) > 0:
            # A right answer with no cited evidence is not a demonstration of
            # research; imitating it teaches answering from memory.
            funnel["ungrounded"] += 1
            continue

        signature = behaviour_signature(trajectory)
        if signature in seen[trajectory.task_id]:
            funnel["duplicate"] += 1
            continue
        if len(by_question[trajectory.task_id]) >= cfg.max_per_question:
            funnel["over_quota"] += 1
            continue

        seen[trajectory.task_id].add(signature)
        by_question[trajectory.task_id].append(trajectory)

    kept = [t for group in by_question.values() for t in group]
    funnel["kept"] = len(kept)
    funnel["questions"] = len(by_question)
    return kept, funnel


def render_funnel(funnel: dict) -> str:
    return "\n".join([
        "SFT 数据筛选",
        f"  采样轨迹              {funnel['total']}",
        f"  − 运行出错             {funnel['errored']}",
        f"  − 未通过验证器         {funnel['below_reward']}",
        f"  − 答对但无引用支撑     {funnel['ungrounded']}",
        f"  − 行为重复             {funnel['duplicate']}",
        f"  − 超过每题配额         {funnel['over_quota']}",
        f"  = 保留                {funnel['kept']}"
        f"  （覆盖 {funnel.get('questions', 0)} 道题）",
    ])


def build_dataset(trajectories: list[Trajectory], tools=None) -> list[packer.Sample]:
    """Tokenise the selected trajectories with the shared packing."""
    samples = []
    for trajectory in trajectories:
        sample = packer.to_sample(trajectory, tools)
        if sample is not None:
            samples.append(sample)
        else:
            logger.debug("dropped %s: not tokenisable", trajectory.task_id)
    return samples
