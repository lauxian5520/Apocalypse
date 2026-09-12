"""The adversarial one-query filter: is this question actually multi-hop?

A multi-hop question only trains search if finding the evidence takes more than
one query. `one_query_solvable()` is the check the pipeline uses: paste the
question straight into BM25 and see whether *every* gold document comes back at
once. Measured on HotpotQA's dev split, 48.3% of questions clear that bar at
k=5, while 95.7% return at least one gold document — the gap between those two
numbers is the part of the task that is genuinely a second hop.

Those questions are **labelled, not dropped** (see `split.py` for why): they are
easy, not hollow, and the online difficulty curriculum retires them once the
policy outgrows them.

Two properties keep this a real guard rather than a ritual:

1. It uses the *same* index, the *same* tokenizer and the *same* query length
   cap the agent's `corpus_search` uses. A filter gentler than the environment
   it guards passes questions the environment then trivialises; one stricter
   models a query the agent could not have issued.
2. It models the *laziest* query, not a clever one. A question that survives
   having its own text pasted verbatim into the index is hard for reasons that
   do not depend on the agent phrasing things well.

`judge()` below is the stricter single-gold form written for the earlier arXiv
corpus, where a question was built from a paraphrased description of one target
document and the risk was the paraphrase leaking its rare terms. It is kept
because that corpus is still in the repository as the evidence behind the
pivot; HotpotQA uses `one_query_solvable`.

All of it is local computation — no model, no network — so it runs before any
LLM-based filter and costs nothing.
"""
import re
from dataclasses import dataclass

from harness.corpus.index import BM25Index, tokenize
# The filter must never issue a query the environment would refuse, or it is
# modelling something the agent cannot do. Mirrors `corpus_search`'s own cap.
from harness.tools.builtin.corpus import MAX_QUERY_CHARS as MAX_PROBE_CHARS

# The gold document must not appear this high for any probe formulation.
DEFAULT_TOP_K = 5

# A description that shares this fraction of its rare terms with the target's
# own text is a near-copy regardless of where it ranks, so it is rejected on
# lexical grounds too. Catches the case where the corpus happens to contain a
# stronger decoy that pushes the gold document to rank 6.
MAX_RARE_TERM_OVERLAP = 0.5

# "Rare" means the term occurs in at most this fraction of the corpus. Common
# words overlap between any two papers on the same topic and say nothing about
# copying.
RARE_DF_FRACTION = 0.01

_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")


@dataclass(frozen=True)
class Probe:
    """One way an agent might turn the question into a query."""

    name: str
    query: str


@dataclass(frozen=True)
class Verdict:
    solvable_in_one_query: bool
    losing_probe: str = ""          # which formulation found it, "" when none did
    best_rank: int = 0              # rank of the gold doc under that probe, 0 = unfound
    rare_overlap: float = 0.0

    @property
    def rejected(self) -> bool:
        return self.solvable_in_one_query or self.rare_overlap > MAX_RARE_TERM_OVERLAP

    def reason(self) -> str:
        if self.solvable_in_one_query:
            return f"一次检索即命中（{self.losing_probe} 第 {self.best_rank} 位）"
        if self.rare_overlap > MAX_RARE_TERM_OVERLAP:
            return f"与目标文本罕见词重合率 {self.rare_overlap:.0%}，描述近似照抄"
        return ""


def probes(question: str, description: str) -> list[Probe]:
    """The query formulations an agent could realistically try.

    `description` is the paraphrased first-hop clause; `question` is the whole
    thing including the second-hop constraints. Both are probed because an
    agent that pastes the lot and an agent that extracts the clause are both
    plausible, and the question has to survive both.
    """
    out = [Probe("整题", question)]
    if description and description != question:
        out.append(Probe("描述子句", description))
        first = _SENTENCE.split(description.strip())[0]
        if first and first != description:
            out.append(Probe("描述首句", first))
    return out


def rare_terms(index: BM25Index, text: str) -> set[str]:
    """Terms in `text` that are rare across the corpus."""
    n = max(len(index.doc_ids), 1)
    ceiling = max(1, int(n * RARE_DF_FRACTION))
    return {
        t for t in set(tokenize(text))
        if 0 < len(index.postings.get(t, ())) <= ceiling
    }


def rare_overlap(index: BM25Index, description: str, target_text: str) -> float:
    """Fraction of the description's rare terms that the target also uses."""
    described = rare_terms(index, description)
    if not described:
        return 0.0
    target = set(tokenize(target_text))
    return len(described & target) / len(described)


def one_query_solvable(
    index: BM25Index,
    question: str,
    gold_doc_ids: list[str],
    k: int = DEFAULT_TOP_K,
) -> bool:
    """Would pasting the question into BM25 surface *all* the evidence at once?

    The multi-hop form of the filter. A question needs several documents, so it
    is only trivially solvable when a single query retrieves every one of them —
    retrieving one gold document still leaves a genuine second hop to do.

    Measured on HotpotQA's dev split: 48.3% of questions clear this bar at k=5,
    while 95.7% retrieve at least one gold document. That gap is the task.

    The question text is used verbatim because that is the laziest thing an
    agent can do, and a question that survives even the lazy query is a question
    whose difficulty does not depend on the agent being clever about phrasing.
    """
    gold = set(gold_doc_ids)
    if not gold:
        return False
    retrieved = {h.doc_id for h in index.search(question[:MAX_PROBE_CHARS], k=k)}
    return gold <= retrieved


def judge(
    index: BM25Index,
    *,
    question: str,
    description: str,
    gold_id: str,
    target_text: str = "",
    k: int = DEFAULT_TOP_K,
) -> Verdict:
    """Decide whether this question survives the adversarial filter.

    `gold_id` is the document the *first* hop must land on — not the answer.
    The bridge is what has to be hard to find; once found, the second hop is
    supposed to be a deterministic metadata lookup.
    """
    worst_rank = 0
    losing = ""
    for probe in probes(question, description):
        for rank, hit in enumerate(index.search(probe.query, k=k), 1):
            if hit.doc_id == gold_id:
                # Record the best (numerically lowest) rank across probes.
                if worst_rank == 0 or rank < worst_rank:
                    worst_rank, losing = rank, probe.name
                break

    overlap = rare_overlap(index, description, target_text) if target_text else 0.0
    return Verdict(
        solvable_in_one_query=worst_rank > 0,
        losing_probe=losing,
        best_rank=worst_rank,
        rare_overlap=overlap,
    )
