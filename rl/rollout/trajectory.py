"""One episode, recorded completely enough to re-grade it without re-running it.

A trajectory stores the **raw event log**, not a summary. That is the whole
payoff of the harness's "everything the model sees is logged first" invariant:
months later, with no corpus loaded and no model reachable, `derive_messages()`
replays the exact prompt the policy saw, and the verifiers re-derive the exact
reward. A summary would make re-scoring under a changed reward function — which
is precisely what the credit-assignment ablation does — impossible.

Every record is stamped with what the environment *was*: corpus hash, preset,
step cap, model. A reward is only meaningful relative to those, and a run
comparing trajectories collected under two different corpora is comparing
nothing. The stamp makes that detectable instead of silent.
"""
import json
from dataclasses import asdict, dataclass, field

from harness.events import SessionEvent


@dataclass
class EnvStamp:
    """What the environment was when this trajectory was collected."""

    corpus_sha256: str
    corpus_docs: int
    preset: str
    max_steps: int
    model: str
    # Set once the training-time adapter exists; pins the chat template so a
    # re-tokenization months later cannot silently use a different one.
    template_sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trajectory:
    """One rollout of one task."""

    task_id: str
    question: str
    gold_answer: str
    events: list[dict] = field(default_factory=list)
    reward: dict = field(default_factory=dict)
    stamp: dict = field(default_factory=dict)
    seconds: float = 0.0
    # Wall clock this episode spent inside the provider, measured as a delta so
    # it is correct whether the adapter is per-episode or shared across many.
    generation_seconds: float = 0.0
    # One entry per generation, in order, from the training-time policy adapter:
    # prompt token ids, completion token ids, and the log-probs recorded *at
    # sampling time*. Empty for evaluation rollouts, which do not need them.
    sampled: list = field(default_factory=list)
    # Set when the episode died of something other than the agent's own choice
    # — a provider outage, a crash. Kept rather than dropped so a run's failure
    # rate is visible instead of quietly shrinking the denominator.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "question": self.question,
            "gold_answer": self.gold_answer,
            "events": self.events,
            "reward": self.reward,
            "stamp": self.stamp,
            "seconds": round(self.seconds, 3),
            "generation_seconds": round(self.generation_seconds, 3),
            "sampled": self.sampled,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Trajectory":
        return cls(
            task_id=raw.get("task_id", ""),
            question=raw.get("question", ""),
            gold_answer=raw.get("gold_answer", ""),
            events=raw.get("events") or [],
            reward=raw.get("reward") or {},
            stamp=raw.get("stamp") or {},
            seconds=float(raw.get("seconds") or 0.0),
            generation_seconds=float(raw.get("generation_seconds") or 0.0),
            sampled=raw.get("sampled") or [],
            error=raw.get("error", ""),
        )

    def session_events(self) -> list[SessionEvent]:
        """Rebuild the log so `derive_messages` and the verifiers can read it."""
        return [
            SessionEvent(
                type=e["type"], seq=int(e["seq"]), time=int(e.get("time") or 0),
                data=e.get("data") or {},
            )
            for e in self.events
        ]


def write_jsonl(path: str, trajectories: list[Trajectory]) -> int:
    with open(path, "w", encoding="utf-8") as f:
        for t in trajectories:
            f.write(json.dumps(t.to_dict(), ensure_ascii=False))
            f.write("\n")
    return len(trajectories)


def read_jsonl(path: str) -> list[Trajectory]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Trajectory.from_dict(json.loads(line)))
    return out
