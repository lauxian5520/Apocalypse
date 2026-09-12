"""BM25 over the frozen corpus — a hand-rolled inverted index, no dependencies.

Why not `bm25s` or Pyserini: this index is part of the *environment definition*.
Every reward in the project is conditioned on what `corpus_search` returns, so a
library upgrade silently changing tokenization or scoring would silently change
the task — and invalidate every number already reported. A hundred lines pinned
in this repository is the cheaper guarantee, and it keeps Phase 1 runnable on a
Raspberry Pi with nothing but the standard library.

Speed is a non-issue at this scale: scoring touches only documents that contain
a query term, so a ten-term query over 50k documents is a few thousand float
updates. The rollout bottleneck is the model, by three orders of magnitude.

The scoring function is textbook Robertson/Sparck-Jones BM25 with the standard
`k1 = 1.5`, `b = 0.75`. The IDF variant is the `+1` smoothed one, which cannot
go negative — the raw form does for terms appearing in more than half the
corpus, which would make a stopword-heavy query score *below* an empty one.
"""
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass

INDEX_NAME = "bm25_index.json"

K1 = 1.5
B = 0.75

# Split on anything that is not a word character. Hyphens and slashes are
# separators on purpose ("encoder-decoder" indexes as two terms), because a
# question paraphrased by a model will rarely reproduce the exact punctuation.
_TOKEN = re.compile(r"[A-Za-z0-9]+")

# Extremely common English function words. This list is deliberately short: an
# aggressive stoplist helps precision on natural questions but also makes the
# "can one query find the gold document" adversarial filter *weaker* than the
# real search the agent performs, and that filter must never be more permissive
# than the environment it is guarding.
STOPWORDS = frozenset("""
a an the and or of for to in on at by with from as is are was were be been being
this that these those it its we our you your they their he she his her i
""".split())

MIN_TOKEN_CHARS = 2


def tokenize(text: str) -> list[str]:
    """The single tokenizer: indexing and querying must never diverge."""
    return [
        t for t in (m.group(0).lower() for m in _TOKEN.finditer(text))
        if len(t) >= MIN_TOKEN_CHARS and t not in STOPWORDS
    ]


@dataclass
class Hit:
    doc_id: str
    score: float


class BM25Index:
    """An inverted index with document-frequency and length statistics."""

    def __init__(self) -> None:
        self.doc_ids: list[str] = []
        self.doc_len: list[int] = []
        # term -> list of (doc ordinal, term frequency)
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.avg_len: float = 0.0

    # ── building ──────────────────────────────────────────────────
    def add(self, doc_id: str, text: str) -> None:
        ordinal = len(self.doc_ids)
        tokens = tokenize(text)
        self.doc_ids.append(doc_id)
        self.doc_len.append(len(tokens))

        freq: dict[str, int] = defaultdict(int)
        for token in tokens:
            freq[token] += 1
        for term, count in freq.items():
            self.postings[term].append((ordinal, count))

    def finalize(self) -> None:
        total = sum(self.doc_len)
        self.avg_len = total / len(self.doc_len) if self.doc_len else 0.0

    # ── querying ──────────────────────────────────────────────────
    def search(self, query: str, k: int = 5) -> list[Hit]:
        """Top-`k` documents for `query`, highest score first.

        Ties break on document ordinal, which is corpus order, which is fixed
        by the frozen file. Determinism here is what lets the check script
        assert that five fixed queries return five fixed id lists.
        """
        n = len(self.doc_ids)
        if n == 0:
            return []

        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = math.log(1.0 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for ordinal, tf in postings:
                norm = 1.0 - B + B * (self.doc_len[ordinal] / self.avg_len) if self.avg_len else 1.0
                scores[ordinal] += idf * (tf * (K1 + 1.0)) / (tf + K1 * norm)

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [Hit(doc_id=self.doc_ids[o], score=round(s, 6)) for o, s in ranked]

    # ── persistence ───────────────────────────────────────────────
    def save(self, path: str) -> None:
        """Persist as JSON.

        JSON rather than pickle: the index is a build artifact that other tools
        and a future reader should be able to open, and an unpickle is a code
        execution surface in a project whose whole point is a sandbox.
        """
        payload = {
            "doc_ids": self.doc_ids,
            "doc_len": self.doc_len,
            "avg_len": self.avg_len,
            "postings": {t: p for t, p in self.postings.items()},
        }
        tmp = path + ".partial"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "BM25Index":
        if not os.path.isfile(path):
            raise FileNotFoundError(f"检索索引不存在：{path}（先运行 corpus index）")
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        index = cls()
        index.doc_ids = payload["doc_ids"]
        index.doc_len = payload["doc_len"]
        index.avg_len = payload["avg_len"]
        index.postings = defaultdict(
            list, {t: [(int(o), int(c)) for o, c in p] for t, p in payload["postings"].items()}
        )
        return index


def build_index(corpus_dir: str) -> BM25Index:
    """Index every document's `searchable()` text — title and body only.

    Never `meta`: see `harness/corpus/schema.py::Doc.searchable`.
    """
    from harness.corpus.store import iter_docs

    index = BM25Index()
    for doc in iter_docs(corpus_dir):
        index.add(doc.doc_id, doc.searchable())
    index.finalize()
    return index


def index_path(corpus_dir: str) -> str:
    return os.path.join(corpus_dir, INDEX_NAME)
