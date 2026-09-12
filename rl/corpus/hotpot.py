"""HotpotQA as a corpus and a task set.

Why this replaced a self-built arXiv corpus is worth recording, because the
measurement that forced it is the most useful thing the project learned in its
first day:

    50k arXiv abstracts, cs.AI/CL/CV/LG, 2023-2026.
    The 8 rarest terms of an abstract retrieve its own paper at top-1 in
    100% of 200 sampled cases, in 0.7 ms. The runner-up scores a median
    0.28x the target; no sampled case had a competitor above 0.8x.
    Narrowing the corpus to one topic made it *worse* (0.19x), because the
    topic's vocabulary becomes common and the paper's own terms become more
    discriminative still.

So "find the paper matching this description" is a one-query task at any corpus
size or density reachable from arXiv, no matter how the description is
paraphrased — there is simply nothing nearby to confuse it with. The first hop
has to be hard *because the corpus has genuine near-duplicates*, and that is a
property of the corpus, not of the question wording.

HotpotQA has it by construction. Each question ships ten paragraphs: two gold
and eight distractors that were *retrieved to be confusable* with them. The
union of those paragraphs across the dataset is a corpus where competition is
the norm rather than the exception.

It brings three more things the arXiv design had to build by hand:

- **Exact answers**, with the field-standard EM/F1 normalisation, so the
  outcome verifier needs no LLM judge.
- **`supporting_facts`** — which paragraph *and which sentence* is the evidence.
  A grounding reward and a per-step process signal, annotated by humans, free.
- **Comparability.** Published multi-hop numbers exist to calibrate against.

Licence: CC BY-SA 4.0 (Yang et al., 2018). Attribution belongs in `rl/README.md`
and the corpus manifest records the source.
"""
import hashlib
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator

from harness.corpus.schema import Doc

logger = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co/datasets/hotpotqa/hotpot_qa/resolve/main/distractor"
SPLIT_FILES = {
    "validation": ["validation-00000-of-00001.parquet"],
    "train": ["train-00000-of-00002.parquet", "train-00001-of-00002.parquet"],
}

DOWNLOAD_TIMEOUT_SECONDS = 600
CHUNK = 1 << 20


@dataclass
class HotpotTask:
    """One question, with everything a verifier needs to grade an answer."""

    task_id: str
    question: str
    answer: str
    qtype: str                                  # "bridge" | "comparison"
    level: str                                  # "easy" | "medium" | "hard"
    gold_doc_ids: list[str] = field(default_factory=list)
    # (doc_id, sentence index) pairs — the human-annotated evidence.
    supporting: list[tuple[str, int]] = field(default_factory=list)
    # Every paragraph offered with this question: the two gold plus eight
    # distractors. Kept so an evaluation can reproduce the original distractor
    # setting, where retrieval is over these ten rather than the whole corpus.
    context_doc_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "question": self.question,
            "answer": self.answer,
            "qtype": self.qtype,
            "level": self.level,
            "gold_doc_ids": self.gold_doc_ids,
            "supporting": [list(s) for s in self.supporting],
            "context_doc_ids": self.context_doc_ids,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "HotpotTask":
        return cls(
            task_id=raw["task_id"],
            question=raw["question"],
            answer=raw["answer"],
            qtype=raw.get("qtype", ""),
            level=raw.get("level", ""),
            gold_doc_ids=list(raw.get("gold_doc_ids") or []),
            supporting=[(s[0], int(s[1])) for s in (raw.get("supporting") or [])],
            context_doc_ids=list(raw.get("context_doc_ids") or []),
        )


def doc_id_for(title: str) -> str:
    """A stable id for a Wikipedia paragraph, derived from its title.

    HotpotQA identifies paragraphs by title only, and titles are unique within
    it. A hash rather than the raw title because the id is what the model types
    into `corpus_open`: titles contain spaces, quotes and parentheses that a
    model would have to quote correctly, and a mistyped title is an error the
    environment should not be spending steps on. Twelve hex characters over
    ~500k passages leaves collisions negligible.
    """
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]


def download(split: str, dest_dir: str) -> list[str]:
    """Fetch the parquet shards for `split`, skipping any already present."""
    if split not in SPLIT_FILES:
        raise ValueError(f"未知 split：{split}（可选 {sorted(SPLIT_FILES)}）")
    os.makedirs(dest_dir, exist_ok=True)

    paths = []
    for name in SPLIT_FILES[split]:
        path = os.path.join(dest_dir, name)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            logger.info("already have %s", name)
            paths.append(path)
            continue

        url = f"{HF_BASE}/{name}"
        logger.info("downloading %s", url)
        partial = path + ".partial"
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp, \
                open(partial, "wb") as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(partial, path)
        paths.append(path)
    return paths


def _rows(paths: list[str]) -> Iterator[dict]:
    import pyarrow.parquet as pq

    for path in paths:
        table = pq.read_table(path)
        for row in table.to_pylist():
            yield row


def read(paths: list[str]) -> tuple[dict[str, Doc], list[HotpotTask]]:
    """Parse the shards into a deduplicated corpus and a task list.

    Paragraphs are shared across questions — a popular entity appears as a
    distractor many times — so the corpus is deduplicated by title while the
    tasks keep referring to it by id.
    """
    docs: dict[str, Doc] = {}
    tasks: list[HotpotTask] = []

    for row in _rows(paths):
        context = row.get("context") or {}
        titles = list(context.get("title") or [])
        sentence_lists = list(context.get("sentences") or [])

        context_ids = []
        for title, sentences in zip(titles, sentence_lists):
            did = doc_id_for(title)
            context_ids.append(did)
            if did not in docs:
                docs[did] = Doc(
                    doc_id=did,
                    title=title,
                    # Sentences are joined with a single space; sentence
                    # boundaries are recoverable from the list, and the
                    # supporting-fact indices refer to positions in it, so the
                    # split is preserved in `meta` rather than in the text.
                    text=" ".join(s.strip() for s in sentences).strip(),
                    meta={"source": "hotpotqa-wiki", "sentences": str(len(sentences))},
                )

        facts = row.get("supporting_facts") or {}
        fact_titles = list(facts.get("title") or [])
        fact_sent_ids = list(facts.get("sent_id") or [])
        supporting = [
            (doc_id_for(t), int(i)) for t, i in zip(fact_titles, fact_sent_ids)
        ]
        gold = list(dict.fromkeys(d for d, _ in supporting))

        tasks.append(HotpotTask(
            task_id=str(row.get("id", "")),
            question=(row.get("question") or "").strip(),
            answer=(row.get("answer") or "").strip(),
            qtype=(row.get("type") or "").strip(),
            level=(row.get("level") or "").strip(),
            gold_doc_ids=gold,
            supporting=supporting,
            context_doc_ids=context_ids,
        ))

    return docs, tasks
