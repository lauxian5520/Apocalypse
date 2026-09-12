"""Composing one scalar reward, and the rule that keeps it from backfiring.

**The rule: shaping is gated behind correctness.** Every shaping term is
multiplied by the outcome, so a wrong answer scores exactly 0 no matter how
tidily it was produced.

The reason is specific and was the single most likely way to waste a training
run. Early in training a 1.5B policy is almost never right, so `outcome` is
almost always 0. An efficiency penalty added to that would be the *only* term
with any gradient, and the cheapest way to maximise it is to stop searching and
answer immediately with nothing. The policy would learn to give up — fast, and
irreversibly, because it then never explores far enough to find a reward. Gating
means the shaping terms only ever distinguish *between correct* trajectories,
which is the only comparison they are meaningful for.

Anti-gaming works the other way: a violation voids the reward outright, right
answer or not. An agent that cites documents it never opened has not done the
task, and paying it partial credit is how a verifier gets farmed.

`weights` is a dataclass rather than constants so the credit-assignment ablation
can run outcome-only, outcome+shaping and attribution variants from one code
path — the ablation table is the deliverable, so switching configuration must
not mean editing this file.
"""
from dataclasses import dataclass, field

from rl.verifiers import outcome as outcome_verifier
from rl.verifiers import process
from rl.verifiers.trace import Trace


@dataclass(frozen=True)
class Weights:
    """How much each gated shaping term can add on top of a correct answer.

    They sum to well under 1 on purpose: a correct answer is worth 1.0 and the
    shaping can move it within [1.0, 1.0 + sum(weights)]. Shaping must never be
    able to outweigh correctness itself.
    """

    grounding: float = 0.15
    efficiency: float = 0.05
    failed_attempt_penalty: float = 0.05    # charged per rejected corpus_answer

    @classmethod
    def outcome_only(cls) -> "Weights":
        """The ablation baseline: pure sparse outcome reward."""
        return cls(grounding=0.0, efficiency=0.0, failed_attempt_penalty=0.0)


@dataclass
class Reward:
    """A full scoring of one trajectory. Every component is kept for analysis."""

    total: float
    outcome: outcome_verifier.Outcome
    fmt: process.Format
    grounding: process.Grounding
    antigaming: process.AntiGaming
    redundant_fraction: float
    steps: int
    tool_calls: int
    ended_by: str
    voided: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        """Correct *and* legitimately earned — what pass@1 should report."""
        return self.outcome.exact_match and not self.voided

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 4),
            "correct": self.correct,
            "exact_match": self.outcome.exact_match,
            "f1": round(self.outcome.f1, 4),
            "voided": self.voided,
            "notes": self.notes,
            "format_valid": self.fmt.valid,
            "failed_answer_attempts": self.fmt.failed_attempts,
            "grounding_f1": round(self.grounding.f1, 4),
            "grounding_recall": round(self.grounding.recall, 4),
            "opened_gold": self.grounding.opened_gold,
            "redundant_query_fraction": round(self.redundant_fraction, 4),
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "ended_by": self.ended_by,
            "predicted": self.outcome.predicted,
            "gold": self.outcome.gold,
        }


def score(trace: Trace, task: dict, weights: Weights | None = None) -> Reward:
    """Grade one trajectory against its task.

    `task` is a HotpotQA task dict as stored beside the corpus: it carries
    `answer` and `gold_doc_ids`.
    """
    w = weights or Weights()

    fmt = process.check_format(trace)
    outcome = outcome_verifier.score(trace.answer or "", task["answer"])
    grounding = process.check_grounding(trace, task.get("gold_doc_ids") or [])
    antigaming = process.check_antigaming(trace, task["answer"], task.get("question", ""))
    redundant = process.redundant_query_fraction(trace.queries)

    notes: list[str] = []
    total = outcome.reward

    if not antigaming.clean:
        # Void, not penalise. A discount would still leave farming profitable
        # whenever the outcome term is larger than the discount.
        notes.extend(antigaming.reasons())
        return Reward(
            total=0.0, outcome=outcome, fmt=fmt, grounding=grounding,
            antigaming=antigaming, redundant_fraction=redundant,
            steps=trace.steps, tool_calls=trace.tool_calls,
            ended_by=trace.ended_by, voided=True, notes=notes,
        )

    if total > 0:
        # Gated: shaping only ever separates correct trajectories from each
        # other. See the module docstring.
        total += w.grounding * grounding.score
        total -= w.efficiency * redundant
        total -= w.failed_attempt_penalty * fmt.failed_attempts
        total = max(0.0, total)

    return Reward(
        total=total, outcome=outcome, fmt=fmt, grounding=grounding,
        antigaming=antigaming, redundant_fraction=redundant,
        steps=trace.steps, tool_calls=trace.tool_calls,
        ended_by=trace.ended_by, notes=notes,
    )


def aggregate(rewards: list[Reward]) -> dict:
    """Run-level metrics. These are what the eval report and the curves show."""
    n = max(len(rewards), 1)
    answered = [r for r in rewards if r.fmt.answered]
    return {
        "n": len(rewards),
        "pass@1": sum(1 for r in rewards if r.correct) / n,
        "exact_match": sum(1 for r in rewards if r.outcome.exact_match) / n,
        "f1": sum(r.outcome.f1 for r in rewards) / n,
        "format_valid": sum(1 for r in rewards if r.fmt.valid) / n,
        "voided": sum(1 for r in rewards if r.voided) / n,
        "gave_up": sum(1 for r in rewards if r.ended_by in ("no-tool-call", "max-steps")) / n,
        "mean_reward": sum(r.total for r in rewards) / n,
        "mean_steps": sum(r.steps for r in rewards) / n,
        "mean_tool_calls": sum(r.tool_calls for r in rewards) / n,
        "mean_grounding_f1": (sum(r.grounding.f1 for r in answered) / len(answered)) if answered else 0.0,
        "mean_redundant_queries": sum(r.redundant_fraction for r in rewards) / n,
    }
