"""Which retrieved document actually mattered? Context-ablation attribution.

Take a finished trajectory, remove one retrieved document from the context, and
measure how much harder the gold answer becomes. The drop is that document's
marginal contribution, giving a dense per-retrieval signal on a task whose
reward arrives once, at the end.

Three things about this are deliberate and each was a correction.

**It is named honestly.** This is *context ablation*, not credit assignment for
the agent's actions. Removing a document produces a context the policy never
generated, so what is measured is "how much does the answer depend on this
evidence being present", not "what was the causal value of the action that
fetched it". The distinction matters when writing it up.

**It scores by log-probability, not by resampling the reward.** With a binary
reward each ablation is one Bernoulli sample, so separating a real effect from
noise needs several resamples per document — roughly 4 x 10 documents = 40 extra
generations per trajectory, which makes the whole method too expensive to run.
`log P(gold answer | context)` is continuous, needs **one forward pass**, and has
far lower variance. That is what makes the cost claim true.

**Removed documents are replaced by an equal-length placeholder**, not deleted.
Deleting shifts every later document's position, so the measurement would be
contaminated by the model's position sensitivity rather than its dependence on
the evidence.

**Use it as a diagnostic before using it as a reward.** The most valuable output
is the shape of the distribution: if the deltas cluster near zero, the policy is
answering without depending on what it retrieved — which means the leakage
filter let memorised questions through, and that is a finding about the dataset,
not a shaping signal to train on.
"""
import logging
import re
from dataclasses import dataclass, field

from harness import events as ev
from harness.events import SessionEvent

from rl.verifiers.trace import DOC_ID, Trace, extract

logger = logging.getLogger(__name__)

# Same visible length as what it replaces is impossible in general; what matters
# is that the surrounding structure and ordering survive. The marker is obvious
# in a transcript so an ablated context is never mistaken for a real one.
PLACEHOLDER = "[文档已省略]"


@dataclass
class Contribution:
    doc_id: str
    delta: float                # logp(gold | full) - logp(gold | ablated)
    baseline_logp: float
    ablated_logp: float
    was_cited: bool = False
    was_gold: bool = False

    @property
    def mattered(self) -> bool:
        """Removing it made the gold answer measurably less likely."""
        return self.delta > 0.0


@dataclass
class Attribution:
    task_id: str
    baseline_logp: float = 0.0
    contributions: list[Contribution] = field(default_factory=list)

    @property
    def total_delta(self) -> float:
        return sum(c.delta for c in self.contributions)

    def top(self, n: int = 3) -> list[Contribution]:
        return sorted(self.contributions, key=lambda c: -c.delta)[:n]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "baseline_logp": round(self.baseline_logp, 4),
            "contributions": [
                {
                    "doc_id": c.doc_id,
                    "delta": round(c.delta, 4),
                    "was_cited": c.was_cited,
                    "was_gold": c.was_gold,
                }
                for c in sorted(self.contributions, key=lambda x: -x.delta)
            ],
        }


def retrieved_doc_ids(trace: Trace) -> list[str]:
    """Documents the agent actually saw, in the order it first saw them."""
    order: list[str] = []
    for call in trace.calls:
        for doc_id in DOC_ID.findall(call.content or ""):
            if doc_id not in order:
                order.append(doc_id)
    return order


def ablate(log: list[SessionEvent], doc_id: str) -> list[SessionEvent]:
    """A copy of the log with every mention of `doc_id` blanked in tool output.

    Only `tool/result` contents are touched: the agent's own messages are what
    it generated, and rewriting them would be fabricating a trajectory rather
    than ablating an observation.

    The whole passage belonging to the id is replaced, not just the id itself —
    leaving the text while removing the identifier would still let the model
    read the evidence.
    """
    pattern = re.compile(
        # A search hit: "N. [id] Title\n   snippet" up to the next hit or the end.
        rf"\d+\.\s*\[{re.escape(doc_id)}\][^\n]*(?:\n(?!\s*\d+\.\s*\[)[^\n]*)*"
        # Or an opened document: everything from "编号 <id>" onward.
        rf"|编号\s*{re.escape(doc_id)}(?:.|\n)*"
    )

    out: list[SessionEvent] = []
    for event in log:
        if event.type != ev.TOOL_RESULT:
            out.append(event)
            continue
        content = event.data.get("content") or ""
        if doc_id not in content:
            out.append(event)
            continue
        data = dict(event.data)
        data["content"] = pattern.sub(PLACEHOLDER, content)
        out.append(SessionEvent(type=event.type, seq=event.seq, time=event.time, data=data))
    return out


def attribute(
    log: list[SessionEvent],
    task: dict,
    score_gold,
    *,
    gold_doc_ids: set[str] | None = None,
) -> Attribution:
    """Ablate each retrieved document and measure the drop in gold log-prob.

    `score_gold(log) -> float` returns `log P(gold answer | context built from
    log)`. It is injected rather than built here so the same logic runs against
    a vLLM server, a local model, or a stub in a test — and so this module needs
    no torch.
    """
    trace = extract(log)
    gold = gold_doc_ids if gold_doc_ids is not None else set(task.get("gold_doc_ids") or [])
    cited = set(trace.citations)

    baseline = score_gold(log)
    result = Attribution(task_id=task.get("task_id", ""), baseline_logp=baseline)

    for doc_id in retrieved_doc_ids(trace):
        ablated_logp = score_gold(ablate(log, doc_id))
        result.contributions.append(Contribution(
            doc_id=doc_id,
            delta=baseline - ablated_logp,
            baseline_logp=baseline,
            ablated_logp=ablated_logp,
            was_cited=doc_id in cited,
            was_gold=doc_id in gold,
        ))
    return result


def diagnose(attributions: list[Attribution]) -> dict:
    """Is the policy's answer actually grounded in what it retrieved?

    The headline is `mean_gold_delta`. Near zero means removing the human-
    annotated supporting evidence did not make the gold answer any less likely —
    i.e. the answer was not coming from the retrieval at all. On a dataset that
    has been leakage-filtered that should not happen, so a near-zero value is
    evidence the filter missed something.
    """
    gold_deltas, other_deltas = [], []
    for attribution in attributions:
        for c in attribution.contributions:
            (gold_deltas if c.was_gold else other_deltas).append(c.delta)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "trajectories": len(attributions),
        "gold_docs_ablated": len(gold_deltas),
        "other_docs_ablated": len(other_deltas),
        "mean_gold_delta": round(mean(gold_deltas), 4),
        "mean_other_delta": round(mean(other_deltas), 4),
        # The separation is the thing: gold evidence should matter more than a
        # distractor the agent happened to look at. If it does not, either the
        # policy is not reading, or the question did not need reading.
        "separation": round(mean(gold_deltas) - mean(other_deltas), 4),
        "gold_mattered_fraction": round(
            sum(1 for d in gold_deltas if d > 0) / len(gold_deltas), 4
        ) if gold_deltas else 0.0,
    }
