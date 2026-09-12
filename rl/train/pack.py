"""Trajectories → padded batches the GRPO loss can consume.

The join between `rl/env/template.py` (one trajectory → ids + mask) and
`rl/train/grpo.py` (a batch → a scalar). Everything here is bookkeeping, but two
pieces of it are the kind that silently corrupt a run.

**Padding must not be trainable.** Pad positions get `mask = 0`, so they
contribute nothing to the loss *and* nothing to its denominator — the loss
divides by the number of unmasked tokens in the batch, so a padded batch and an
unpadded one give the same answer. Getting this wrong makes the effective
learning rate depend on how ragged the batch happened to be.

**Group membership is the question id, not the batch position.** GRPO's baseline
is the mean reward of the other rollouts *of the same question*. Grouping by
anything else — adjacency, ordering — silently compares a question against
unrelated ones and destroys the variance reduction that is the whole point.

`logp_old` is carried through from the rollout, never recomputed. See the note
in `grpo.py` about why recomputing makes clipping quietly dead.
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # annotations only; see the note below
    from rl.rollout.trajectory import Trajectory

# `Trajectory`, `ToolRegistry` and `template` are imported lazily inside the
# functions that need them, because
# tokenise, because they pull in the whole harness (and a tokenizer). The
# `harness/__init__.py` is an eager facade: importing *any* harness submodule
# runs it, which reaches `harness.context` and from there the DB layer and
# `core.config`. The arithmetic here — padding, grouping, `group_advantages` —
# is plain Python, and keeping it importable without that chain is what lets the
# advantage implementation be checked against the torch one on a machine that
# has torch but not the harness's dependencies.

# Trajectories longer than this are dropped rather than truncated: truncation
# would cut a trajectory mid-turn, leaving an assistant span whose stop token is
# gone, which is exactly the "never learns to stop" failure the mask work
# exists to prevent.
MAX_SEQUENCE_TOKENS = 8192

IGNORE_INDEX = -100


@dataclass
class Sample:
    """One trajectory, tokenised and scored."""

    task_id: str
    token_ids: list[int]
    mask: list[int]
    reward: float
    logp_old: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.token_ids)


@dataclass
class Batch:
    """A padded batch. Tensors are built lazily so this module needs no torch."""

    samples: list[Sample] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.samples)

    @property
    def max_len(self) -> int:
        return max((len(s) for s in self.samples), default=0)

    def group_ids(self) -> list[int]:
        """Integer group index per sample, keyed on `task_id`."""
        order: dict[str, int] = {}
        out = []
        for sample in self.samples:
            out.append(order.setdefault(sample.task_id, len(order)))
        return out

    def to_tensors(self, pad_token_id: int, device: str = "cpu"):
        """(input_ids, mask, rewards, group_ids, logp_old) as torch tensors."""
        import torch

        width = self.max_len
        input_ids, masks, logps = [], [], []
        for sample in self.samples:
            pad = width - len(sample)
            input_ids.append(sample.token_ids + [pad_token_id] * pad)
            # Pad is masked out: contributes to neither numerator nor
            # denominator of the token-level loss.
            masks.append(sample.mask + [0] * pad)
            lp = sample.logp_old or [0.0] * len(sample)
            logps.append(lp + [0.0] * (width - len(lp)))

        return (
            torch.tensor(input_ids, dtype=torch.long, device=device),
            torch.tensor(masks, dtype=torch.long, device=device),
            torch.tensor([s.reward for s in self.samples], dtype=torch.float, device=device),
            torch.tensor(self.group_ids(), dtype=torch.long, device=device),
            torch.tensor(logps, dtype=torch.float, device=device),
        )


def to_sample(trajectory: "Trajectory", tools: list[dict] | None = None) -> Sample | None:
    """Tokenise one trajectory. Returns None when it cannot be trained on."""
    from rl.env import template

    if not trajectory.ok:
        return None

    packed = template.pack(trajectory.session_events(), tools)
    if not packed.segments:
        # No assistant span — nothing the policy produced, nothing to learn from.
        return None
    if len(packed.token_ids) > MAX_SEQUENCE_TOKENS:
        return None

    return Sample(
        task_id=trajectory.task_id,
        token_ids=packed.token_ids,
        mask=packed.mask,
        reward=float(trajectory.reward.get("total") or 0.0),
        logp_old=_place_logprobs(packed, trajectory.sampled),
    )


def _place_logprobs(packed, sampled: list) -> list[float]:
    """Lay the sampler's recorded log-probs onto the full packed sequence.

    Each record carries the `prompt_token_ids` its generation was conditioned
    on, and an assistant span's prompt is exactly `token_ids[:span.start]` — so
    records are matched to spans **by that prefix**, not by position. Position
    is unusable: concurrent rollouts finish out of order.

    A span with no matching record is left at zero and the whole vector is
    dropped, so `PolicyModel._sampler_logprobs` recomputes with a frozen pass
    (ratio exactly 1 for that sample). That is the honest fallback — it makes
    the sample behave like a first on-policy step instead of pairing it with
    some other generation's numbers, which would be silent and wrong.
    """
    if not sampled or not packed.segments:
        return []

    by_prompt = {
        tuple(record.get("prompt_token_ids") or ()): record
        for record in sampled
    }
    logp = [0.0] * len(packed.token_ids)
    matched = 0
    for span in packed.segments:
        record = by_prompt.get(tuple(packed.token_ids[: span.start]))
        if record is None:
            continue
        # `logp_old[t]` is the log-prob *of* token t, and a span holds exactly
        # the completion's tokens, so they line up one-for-one from span.start.
        for offset, value in enumerate(record.get("logp_old") or []):
            position = span.start + offset
            if position < span.end:
                logp[position] = float(value)
        matched += 1

    # Partial coverage is not usable: a ratio of 1 on some spans and a real
    # ratio on others within one sequence weights them incomparably.
    return logp if matched == len(packed.segments) else []


def build_batches(
    trajectories: list["Trajectory"],
    batch_size: int = 8,
    tools: list[dict] | None = None,
) -> list[Batch]:
    """Tokenise and group trajectories into batches.

    Rollouts of the same question are kept **together in one batch**. GRPO
    normalises within a group, so splitting a group across batches would leave
    each part with a baseline computed from a handful of its members — higher
    variance, and for a group of size 1 no signal at all.
    """
    if tools is None:
        from harness.tools.registry import ToolRegistry

        from rl.env.build import PRESET
        tools = ToolRegistry(PRESET).schemas()

    by_task: dict[str, list[Sample]] = {}
    for trajectory in trajectories:
        sample = to_sample(trajectory, tools)
        if sample is not None:
            by_task.setdefault(sample.task_id, []).append(sample)

    batches: list[Batch] = []
    current = Batch()
    for group in by_task.values():
        if current.size and current.size + len(group) > batch_size:
            batches.append(current)
            current = Batch()
        current.samples.extend(group)
    if current.size:
        batches.append(current)
    return batches


def group_advantages(
    rewards: list[float],
    group_ids: list[int],
    min_group_std: float = 1e-6,
    eps: float = 1e-4,
) -> tuple[list[float], list[bool]]:
    """Group-normalised advantages, in plain Python. Returns (advantages, keep).

    The canonical definition. `grpo.group_advantages` is the same computation on
    tensors, for callers that already have them; a check stage asserts the two
    agree numerically, because two implementations that silently drift apart
    would mean the loop trains on different advantages than the loss was
    verified against.

    Pure Python rather than torch so the training loop — and therefore its
    curriculum, metrics and warnings — is exercisable on a machine with no GPU.
    The arithmetic is a mean and a standard deviation over G ≈ 8 numbers; there
    is nothing to accelerate.
    """
    from collections import defaultdict

    members: dict[int, list[int]] = defaultdict(list)
    for index, gid in enumerate(group_ids):
        members[gid].append(index)

    advantages = [0.0] * len(rewards)
    keep = [True] * len(rewards)

    for indices in members.values():
        group = [rewards[i] for i in indices]
        mean = sum(group) / len(group)
        # Population standard deviation, matching torch's `unbiased=False`.
        variance = sum((r - mean) ** 2 for r in group) / len(group)
        std = variance ** 0.5

        if std < min_group_std:
            # Nobody solved it, or everybody did. Either way every advantage in
            # the group is zero and the gradient is zero; the curriculum retires
            # such questions, and here they are simply dropped.
            for i in indices:
                keep[i] = False
            continue
        for i in indices:
            advantages[i] = (rewards[i] - mean) / (std + eps)

    return advantages, keep


def summarise(batches: list[Batch]) -> dict:
    samples = [s for b in batches for s in b.samples]
    if not samples:
        return {"batches": 0, "samples": 0}
    total = sum(len(s) for s in samples)
    trainable = sum(sum(s.mask) for s in samples)
    groups = {s.task_id for s in samples}
    return {
        "batches": len(batches),
        "samples": len(samples),
        "groups": len(groups),
        "mean_group_size": round(len(samples) / len(groups), 2),
        "mean_tokens": round(total / len(samples), 1),
        "trainable_fraction": round(trainable / max(total, 1), 4),
        "mean_reward": round(sum(s.reward for s in samples) / len(samples), 4),
    }
