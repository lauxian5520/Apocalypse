"""Everything about a trajectory except whether the answer string was right.

Three separable questions, kept apart so an ablation can switch one off without
touching the others:

- **format** — did the agent produce a well-formed answer at all?
- **grounding** — did it cite the evidence the humans annotated?
- **anti-gaming** — did it earn the answer, or fake it?

The third is the one that matters most and is easiest to get wrong. An agent
that reaches the right string without retrieving anything is not solving the
task; it is reciting pretraining data, and rewarding it teaches exactly the
behaviour the environment exists to avoid. `provenance` catches the strong
version (an id the agent never saw) and `answer_known_before_evidence` catches
the interesting version (the agent typed the answer into a query before any
document containing it came back).
"""
import re
from dataclasses import dataclass, field

from rl.verifiers.outcome import normalize
from rl.verifiers.trace import Trace

# Citing more than this many documents for a two-hop question is not evidence,
# it is hedging: cite everything and one of them is bound to be right. HotpotQA
# answers are supported by exactly two paragraphs.
MAX_REASONABLE_CITATIONS = 6

# Two queries this similar are the same query. Used for the redundancy penalty.
NEAR_DUPLICATE_JACCARD = 0.85


@dataclass
class Format:
    answered: bool
    has_citations: bool
    failed_attempts: int

    @property
    def valid(self) -> bool:
        return self.answered and self.has_citations

    @property
    def score(self) -> float:
        return 1.0 if self.valid else 0.0


@dataclass
class Grounding:
    """Measured against HotpotQA's human-annotated `supporting_facts`."""

    cited: set[str] = field(default_factory=set)
    gold: set[str] = field(default_factory=set)
    opened_gold: int = 0

    @property
    def recall(self) -> float:
        """Fraction of gold documents the agent cited."""
        return len(self.cited & self.gold) / len(self.gold) if self.gold else 0.0

    @property
    def precision(self) -> float:
        return len(self.cited & self.gold) / len(self.cited) if self.cited else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def score(self) -> float:
        return self.f1


@dataclass
class AntiGaming:
    """Violations. Any one of these voids the outcome reward."""

    fabricated_citations: set[str] = field(default_factory=set)
    citation_spam: bool = False
    answer_known_before_evidence: bool = False

    @property
    def clean(self) -> bool:
        return not (self.fabricated_citations or self.citation_spam
                    or self.answer_known_before_evidence)

    def reasons(self) -> list[str]:
        out = []
        if self.fabricated_citations:
            out.append(f"引用了从未出现过的编号 {sorted(self.fabricated_citations)[:3]}")
        if self.citation_spam:
            out.append(f"引用条数超过 {MAX_REASONABLE_CITATIONS}，属于灌水")
        if self.answer_known_before_evidence:
            out.append("在任何含答案的文档返回之前就把答案写进了检索词——靠记忆而非检索")
        return out


def check_format(trace: Trace) -> Format:
    return Format(
        answered=trace.answered,
        has_citations=bool(trace.citations),
        failed_attempts=trace.failed_answer_attempts,
    )


def check_grounding(trace: Trace, gold_doc_ids: list[str]) -> Grounding:
    gold = set(gold_doc_ids)
    return Grounding(
        cited=set(trace.citations),
        gold=gold,
        opened_gold=len(gold & set(trace.opened)),
    )


def check_antigaming(trace: Trace, gold_answer: str, question: str = "") -> AntiGaming:
    cited = set(trace.citations)

    # An id the agent never saw in any tool result cannot have been read. Either
    # it was invented or copied from memory; both are disqualifying.
    fabricated = cited - trace.seen_doc_ids

    return AntiGaming(
        fabricated_citations=fabricated,
        citation_spam=len(cited) > MAX_REASONABLE_CITATIONS,
        answer_known_before_evidence=_answer_before_evidence(trace, gold_answer, question),
    )


def _answer_before_evidence(trace: Trace, gold_answer: str, question: str = "") -> bool:
    """Did the agent type the answer before any document containing it came back?

    Walks the calls in order, tracking whether the answer string has appeared in
    any tool result yet. A query containing the answer *after* it was retrieved
    is ordinary verification behaviour and fine; before, it could only have come
    from the model's own memory.

    Two exemptions, both found by auditing real trajectories rather than
    reasoned about in advance:

    - **Yes/no answers.** "yes" occurs in ordinary English constantly, so
      matching on it would flag nearly every trajectory.
    - **Answers already named in the question.** HotpotQA's *comparison*
      questions — a fifth of the dataset — are shaped "Which is X, A or B?",
      so the answer is a literal substring of the prompt. Searching for it is
      the obvious correct move, not recall of pretraining data. Without this
      exemption the rule voided a trajectory that had cited exactly the right
      documents (grounding F1 = 1.0), and it would have zeroed that whole slice
      of the task distribution during training while looking like the policy
      simply could not do comparisons.
    """
    gold = normalize(gold_answer)
    if not gold or gold in ("yes", "no") or len(gold) < 4:
        return False
    if question and gold in normalize(question):
        return False

    seen_in_evidence = False
    for call in trace.calls:
        if call.name == "corpus_search":
            query = call.arguments.get("query")
            if isinstance(query, str) and gold in normalize(query) and not seen_in_evidence:
                return True
        if call.content and gold in normalize(call.content):
            seen_in_evidence = True
    return False


def redundant_query_fraction(queries: list[str]) -> float:
    """Fraction of queries that near-repeat an earlier one."""
    if len(queries) < 2:
        return 0.0

    seen: list[set[str]] = []
    redundant = 0
    for query in queries:
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        if not tokens:
            continue
        if any(_jaccard(tokens, prev) >= NEAR_DUPLICATE_JACCARD for prev in seen):
            redundant += 1
        seen.append(tokens)
    return redundant / len(queries)


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0
