"""Loading, verifying and querying a frozen corpus.

`CorpusStore` is what the tool handler holds. It is built once per process and
cached, because a 50k-document corpus plus its inverted index is tens of
megabytes and every rollout in a batch shares the same immutable copy.

The manifest check is not optional hygiene. Every reward in the project is
conditioned on what `corpus_search` returns, so a corpus that drifted from the
manifest silently invalidates every number already reported — and it drifts
invisibly, because a changed document still loads fine. `verify()` is therefore
called at load time, not only from the check script.
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from harness.corpus.index import BM25Index, Hit, index_path
from harness.corpus.schema import DOCS_NAME, MANIFEST_NAME, TASKS_NAME, Doc

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
HASH_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True)
class Manifest:
    """What a corpus is, in enough detail to rebuild it.

    `source` names the builder ("hotpotqa", "arxiv-api", "arxiv-snapshot") and
    `params` carries whatever that builder needed — the split for HotpotQA, the
    categories and date range for arXiv. A free-form dict rather than typed
    fields because the manifest has to survive a change of corpus without a
    schema migration, and nothing reads `params` except a human and the run
    record it is stamped into.
    """

    version: int
    source: str
    doc_count: int
    docs_sha256: str
    built_at: str
    params: dict = field(default_factory=dict)
    task_count: int = 0
    tasks_sha256: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "source": self.source,
            "doc_count": self.doc_count,
            "docs_sha256": self.docs_sha256,
            "built_at": self.built_at,
            "params": self.params,
            "task_count": self.task_count,
            "tasks_sha256": self.tasks_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Manifest":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @property
    def short_hash(self) -> str:
        return self.docs_sha256[:16]

    def describe(self) -> str:
        extra = " · ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        tail = f" · 任务 {self.task_count}" if self.task_count else ""
        return f"{self.source} · {self.doc_count} 篇{tail} · {self.short_hash}…" + (f" · {extra}" if extra else "")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def docs_path(corpus_dir: str) -> str:
    return os.path.join(corpus_dir, DOCS_NAME)


def manifest_path(corpus_dir: str) -> str:
    return os.path.join(corpus_dir, MANIFEST_NAME)


def load_manifest(corpus_dir: str) -> Manifest:
    path = manifest_path(corpus_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"语料清单不存在：{path}（先运行 python -m rl.cli corpus build）")
    with open(path, "r", encoding="utf-8") as f:
        return Manifest.from_dict(json.load(f))


def iter_docs(corpus_dir: str) -> Iterator[Doc]:
    path = docs_path(corpus_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"语料不存在：{path}（先运行 python -m rl.cli corpus build）")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Doc.from_dict(json.loads(line))


def verify(corpus_dir: str) -> Manifest:
    """Re-hash the corpus and check it against its manifest."""
    manifest = load_manifest(corpus_dir)
    actual = sha256_file(docs_path(corpus_dir))
    if actual != manifest.docs_sha256:
        raise RuntimeError(
            f"语料哈希不匹配：清单记录 {manifest.short_hash}…，实际 {actual[:16]}…"
            f"——语料已被改动，基于它得到的结果都不再可比"
        )
    return manifest


class CorpusStore:
    """A loaded corpus: documents by id, plus the BM25 index over them."""

    def __init__(self, corpus_dir: str, manifest: Manifest, docs: dict[str, Doc], index: BM25Index):
        self.corpus_dir = corpus_dir
        self.manifest = manifest
        self._docs = docs
        self.index = index

    # ── access ────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._docs)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._docs

    def get(self, doc_id: str) -> Doc | None:
        return self._docs.get(doc_id)

    def all_docs(self) -> list[Doc]:
        return list(self._docs.values())

    def search(self, query: str, k: int = 5) -> list[Hit]:
        return self.index.search(query, k=k)

    def tasks(self) -> list[dict]:
        """The task set shipped beside this corpus, empty when it has none."""
        path = os.path.join(self.corpus_dir, TASKS_NAME)
        if not os.path.isfile(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # ── loading ───────────────────────────────────────────────────
    @classmethod
    def load(cls, corpus_dir: str, *, check_hash: bool = True) -> "CorpusStore":
        manifest = verify(corpus_dir) if check_hash else load_manifest(corpus_dir)
        docs = {d.doc_id: d for d in iter_docs(corpus_dir)}
        index = BM25Index.load(index_path(corpus_dir))

        # The index and the documents are built from the same file but written
        # by two different commands, so they can fall out of step if only one
        # was re-run. Catching it here turns a confusing "search returns ids
        # that cannot be opened" into one clear message.
        missing = [i for i in index.doc_ids[:50] if i not in docs]
        if missing:
            raise RuntimeError(
                f"索引与语料不一致：索引里的 {missing[0]} 不在语料中"
                f"——请重新运行 python -m rl.cli corpus index"
            )
        if len(index.doc_ids) != len(docs):
            raise RuntimeError(
                f"索引与语料篇数不一致：索引 {len(index.doc_ids)} 篇，语料 {len(docs)} 篇"
                f"——请重新运行 python -m rl.cli corpus index"
            )
        return cls(corpus_dir, manifest, docs, index)


@lru_cache(maxsize=2)
def load_cached(corpus_dir: str, check_hash: bool = True) -> CorpusStore:
    """One shared immutable store per process.

    Hashing a 100 MB corpus costs about a second, and every rollout in a batch
    would otherwise pay it. Cached by directory so a check script can hold a
    pilot corpus and the real one at once.
    """
    store = CorpusStore.load(corpus_dir, check_hash=check_hash)
    logger.info(
        "[corpus] loaded %d docs from %s (sha256 %s…)",
        len(store), corpus_dir, store.manifest.short_hash,
    )
    return store
