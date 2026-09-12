"""Which questions are still worth training on, re-decided as the policy improves.

Under GRPO a question contributes nothing once every rollout of it scores the
same: the group's mean *is* each sample, every advantage is zero, and the
gradient from that group is exactly zero. Those questions still cost a full G
rollouts each, which at 8 samples of a 12-step episode is most of the step's
wall clock spent on a guaranteed no-op.

The naive fix — measure pass rates once with a strong teacher and drop the
extremes — is wrong twice over:

- **Measured against the wrong model.** A question the teacher solves 4/8 times
  may be 0/8 for a 1.5B policy. Difficulty has to be calibrated against the
  policy being trained.
- **Measured once.** The useful band moves. Today's 0/8 question becomes
  tomorrow's 3/8 as the policy improves — that is exactly the question you want
  then — while today's 4/8 becomes 8/8 and stops teaching anything. A static
  filter throws away its best future data and keeps its worst.

So pass rates are re-estimated continuously from rollouts the run already
collected. The rewards are free; they were computed to train on. Retirement is
reversible: a question that goes quiet can come back when the estimate says so.
"""
from dataclasses import dataclass, field

# Questions whose estimated pass rate sits outside this band produce no
# gradient. The bounds are exclusive: exactly-0 and exactly-1 are the
# degenerate cases, and a little margin keeps a question that is one lucky
# rollout away from degenerate in the pool.
ACTIVE_LOW = 0.05
ACTIVE_HIGH = 0.95

# How many recent rollouts of a question the estimate is based on. Short enough
# to track a moving policy, long enough that one unlucky group does not retire a
# good question.
WINDOW = 16

# Below this many observations a question is assumed active rather than judged.
# Retiring on a single group of 8 would throw out most of the training set in
# the first few steps, when the policy is at its worst.
MIN_OBSERVATIONS = 8


@dataclass
class QuestionStats:
    task_id: str
    outcomes: list[int] = field(default_factory=list)   # 1 = solved, 0 = not

    def observe(self, solved: bool) -> None:
        self.outcomes.append(1 if solved else 0)
        if len(self.outcomes) > WINDOW:
            del self.outcomes[: len(self.outcomes) - WINDOW]

    @property
    def observations(self) -> int:
        return len(self.outcomes)

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(self.outcomes) / len(self.outcomes)

    @property
    def active(self) -> bool:
        """Whether this question can still produce a gradient."""
        if self.observations < MIN_OBSERVATIONS:
            return True
        return ACTIVE_LOW < self.pass_rate < ACTIVE_HIGH


class CurriculumPool:
    """The training pool, with per-question pass-rate estimates."""

    def __init__(self, task_ids: list[str]):
        self._stats: dict[str, QuestionStats] = {
            task_id: QuestionStats(task_id) for task_id in task_ids
        }

    def __len__(self) -> int:
        return len(self._stats)

    def observe_group(self, task_id: str, rewards: list[float], threshold: float = 1.0) -> None:
        """Record one group's outcomes for a question."""
        stats = self._stats.get(task_id)
        if stats is None:
            stats = self._stats[task_id] = QuestionStats(task_id)
        for reward in rewards:
            stats.observe(reward >= threshold)

    def active_ids(self) -> list[str]:
        return [task_id for task_id, s in self._stats.items() if s.active]

    def retired_ids(self) -> list[str]:
        return [task_id for task_id, s in self._stats.items() if not s.active]

    def sample(self, n: int, rng) -> list[str]:
        """`n` question ids to roll out next, drawn from the active pool.

        Falls back to the whole pool when the active set has collapsed — an
        empty batch would stall the run, and a collapsed pool is a signal to
        report rather than a reason to stop.
        """
        pool = self.active_ids() or list(self._stats)
        if n >= len(pool):
            return list(pool)
        return rng.sample(pool, n)

    def summary(self) -> dict:
        judged = [s for s in self._stats.values() if s.observations >= MIN_OBSERVATIONS]
        active = self.active_ids()
        return {
            "pool": len(self._stats),
            "active": len(active),
            "retired": len(self._stats) - len(active),
            "judged": len(judged),
            "too_easy": sum(1 for s in judged if s.pass_rate >= ACTIVE_HIGH),
            "too_hard": sum(1 for s in judged if s.pass_rate <= ACTIVE_LOW),
            "mean_pass_rate": (
                round(sum(s.pass_rate for s in judged) / len(judged), 4) if judged else 0.0
            ),
        }
