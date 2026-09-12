"""How much paraphrasing does the first hop actually need?

The adversarial filter rejects questions BM25 can answer in one query. That is
only useful if a *survivable* formulation exists — a filter that rejects
everything is a broken environment, not a strict one. This module measures the
gradient between the extremes, before any model is asked to write anything.

**Every probe is clipped to what `corpus_search` would actually accept.** An
earlier version fed whole abstracts and reported a tidy 100% hit rate, which
measured nothing: the tool rejects anything past `MAX_QUERY_CHARS`, so that
query cannot occur in the environment. A calibration that models a query the
agent cannot issue is not a calibration. The clip also happens to make the
sweep five times faster, because BM25 cost scales with distinct query terms.

The formulations, weakest paraphrase to strongest:

- **title** — the strongest possible leak; near 100% by construction.
- **abstract first sentence** — what a lazy "description" looks like.
- **short query** — the realistic case: a handful of content words, which is
  what an agent actually types.
- **rare-terms stripped** — a crude paraphrase proxy. It *deletes* corpus-rare
  terms and keeps everything else, where a real paraphrase *substitutes*. So it
  is a lower bound on what paraphrasing buys, not an estimate of it.

The gap between the middle rows and the last is the budget the paraphrase step
has to work inside. If stripping rare terms barely moves the hit rate, the
topic itself is identifying and no paraphrase will save it — a signal to change
the template, not the prompt.

No network, no model, runs on a Pi.
"""
import random
import re
import time
from dataclasses import dataclass

from harness.corpus.index import BM25Index, tokenize
from harness.corpus.schema import Doc
from harness.tools.builtin.corpus import MAX_QUERY_CHARS

# How many content words a realistic agent query carries. Measured against the
# environment, not guessed: a model told to "extract the key terms" writes
# roughly this many.
SHORT_QUERY_TERMS = 8

RARE_DF_FRACTION = 0.01


@dataclass
class Row:
    name: str
    hits_at_1: int
    hits_at_5: int
    total: int
    seconds: float = 0.0

    def rate1(self) -> float:
        return self.hits_at_1 / max(self.total, 1)

    def rate5(self) -> float:
        return self.hits_at_5 / max(self.total, 1)

    def ms_per_query(self) -> float:
        return 1000.0 * self.seconds / max(self.total, 1)


def clip(text: str) -> str:
    """Exactly the truncation `corpus_search` enforces."""
    return text.strip()[:MAX_QUERY_CHARS]


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0] if parts else text


def strip_rare(index: BM25Index, text: str, df_fraction: float = RARE_DF_FRACTION) -> str:
    """Delete corpus-rare terms — the crude paraphrase proxy."""
    n = max(len(index.doc_ids), 1)
    ceiling = max(1, int(n * df_fraction))
    return " ".join(t for t in tokenize(text) if len(index.postings.get(t, ())) > ceiling)


def short_query(index: BM25Index, text: str, terms: int = SHORT_QUERY_TERMS) -> str:
    """The `terms` rarest words — what a competent agent would pick out.

    Rarest-first rather than first-N: an agent extracting "key terms" chooses
    distinctive ones, so this is the *strongest* short query available and
    therefore the right thing for an adversarial calibration to model.
    """
    counted = [(len(index.postings.get(t, ())), t) for t in dict.fromkeys(tokenize(text))]
    present = sorted((df, t) for df, t in counted if df > 0)
    return " ".join(t for _, t in present[:terms])


def run(index: BM25Index, docs: list[Doc], sample: int = 200, seed: int = 0) -> list[Row]:
    rng = random.Random(seed)
    chosen = rng.sample(docs, min(sample, len(docs)))

    formulations = {
        "标题原文": lambda d: d.title,
        "正文首句": lambda d: first_sentence(d.text),
        "短查询·最罕见 8 词": lambda d: short_query(index, d.text),
        "正文首句·去罕见词": lambda d: strip_rare(index, first_sentence(d.text)),
        "短查询·去罕见词后取 8 词": lambda d: short_query(index, strip_rare(index, d.text)),
    }

    rows = []
    for name, build in formulations.items():
        at1 = at5 = 0
        started = time.monotonic()
        for doc in chosen:
            query = clip(build(doc))
            if not query.strip():
                continue
            ids = [h.doc_id for h in index.search(query, k=5)]
            if ids and ids[0] == doc.doc_id:
                at1 += 1
            if doc.doc_id in ids:
                at5 += 1
        rows.append(Row(name, at1, at5, len(chosen), time.monotonic() - started))
    return rows


def _display_width(text: str) -> int:
    """East-asian-aware width, so the columns line up in a terminal."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def render(rows: list[Row]) -> str:
    width = max(_display_width(r.name) for r in rows) + 2
    head = "查询构造"
    out = [
        head + " " * (width - _display_width(head)) + "  top-1     top-5     ms/查询",
        "-" * (width + 28),
    ]
    for r in rows:
        pad = " " * (width - _display_width(r.name))
        out.append(f"{r.name}{pad}  {r.rate1():>6.1%}  {r.rate5():>7.1%}  {r.ms_per_query():>9.1f}")
    return "\n".join(out)
