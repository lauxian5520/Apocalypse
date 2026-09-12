"""Freeze a corpus to disk — the *write* side of `harness/corpus/`.

The manifest is not bookkeeping, it is the reproducibility claim. Every number
the project reports is conditioned on a corpus, so `sha256(docs.jsonl)` goes
into every trajectory record and every result table.

Reading, verifying and querying live in `harness/corpus/store.py`, because the
tool handler needs them at rollout time and this module is an offline job. The
`Manifest` dataclass is imported from there rather than redefined so the writer
cannot drift from the reader.

Writing is streaming and atomic: records append to a `.partial` file and the
manifest is written last, so a build killed halfway leaves no manifest and
`load()` fails loudly instead of quietly reading a truncated corpus.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable

from harness.corpus.schema import TASKS_NAME, Doc
from harness.corpus.store import (
    MANIFEST_VERSION,
    Manifest,
    docs_path,
    manifest_path,
    sha256_file,
)

logger = logging.getLogger(__name__)


def _write_jsonl(path: str, rows: Iterable[dict]) -> int:
    partial = path + ".partial"
    count = 0
    with open(partial, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
            count += 1
            if count % 20000 == 0:
                logger.info("wrote %d rows to %s", count, os.path.basename(path))
    if count == 0:
        os.remove(partial)
        return 0
    os.replace(partial, path)
    return count


def write_corpus(
    out_dir: str,
    docs: Iterable[Doc],
    *,
    source: str,
    params: dict | None = None,
    tasks: Iterable[dict] | None = None,
) -> Manifest:
    """Stream `docs` (and optionally `tasks`) to `out_dir`, then pin them."""
    os.makedirs(out_dir, exist_ok=True)

    seen: set[str] = set()

    def unique() -> Iterable[dict]:
        # Deduplicate by id: a duplicate would give one passage two BM25
        # entries and make "exactly one match" reasoning unsound.
        for doc in docs:
            if not doc.doc_id or doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            yield doc.to_dict()

    doc_count = _write_jsonl(docs_path(out_dir), unique())
    if doc_count == 0:
        raise RuntimeError("语料为空，没有写出任何文档——请检查来源与筛选条件")

    task_count, tasks_hash = 0, ""
    if tasks is not None:
        tasks_file = os.path.join(out_dir, TASKS_NAME)
        task_count = _write_jsonl(tasks_file, tasks)
        if task_count:
            tasks_hash = sha256_file(tasks_file)

    manifest = Manifest(
        version=MANIFEST_VERSION,
        source=source,
        doc_count=doc_count,
        docs_sha256=sha256_file(docs_path(out_dir)),
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        params=params or {},
        task_count=task_count,
        tasks_sha256=tasks_hash,
    )
    with open(manifest_path(out_dir), "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest
