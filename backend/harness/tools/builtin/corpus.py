"""The Deep Research environment's three tools.

Unlike every other builtin, these do not touch the workspace. The corpus is
immutable, shared by every concurrent rollout, and lives outside the sandbox on
purpose: `Workspace.resolve()` exists to stop an agent writing where it should
not, and there is nothing to write here. Copying a 100 MB corpus into each
session's workspace to satisfy a rule that guards writes would be cargo cult.

Three design decisions are load-bearing and easy to undo by accident:

1. **`corpus_search` returns a snippet, never a document's `meta`.** The split
   between what `search` shows and what `open` reveals is what forces a second
   hop: a searchable proper noun collapses a two-hop question into one BM25
   query. Measured on an earlier arXiv corpus, indexing metadata made the 8
   rarest terms of a passage retrieve it at top-1 100% of the time. See
   `harness/corpus/schema.py::Doc.searchable`.
2. **`corpus_answer` returns no correctness signal.** If the acknowledgement
   told the model whether it was right, the model would call it repeatedly and
   turn the verifier into an oracle — reward hacking handed over for free. The
   reply is a fixed string.
3. **Arguments are validated here.** `ToolRegistry` checks argument *names*
   against the handler signature and nothing else (`registry.py`
   `_reject_unknown_arguments`); the JSON Schema in the contract is advisory.
   A model that sends `citations` as a bare string rather than a list must get
   a readable error, not a `TypeError` rendered as a stack trace.

Failures return text, never raise past `ToolRegistry.execute` — a missing
corpus is a reduced feature with a clear message, matching how every other
optional dependency in this repository degrades.
"""
import logging

from core.config import get_settings
from core.errors import ValidationError
from harness.tools.base import ToolContext

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_SEARCH_RESULTS = 10
DEFAULT_SEARCH_RESULTS = 5
SNIPPET_CHARS = 200
MAX_QUERY_CHARS = 400
MAX_ANSWER_CHARS = 2000
MAX_CITATIONS = 20

# Deliberately free of any verdict — see the module docstring.
ANSWER_ACK = "答案已记录，本轮结束。"

CORPUS_MISSING = (
    "语料尚未构建，corpus_* 工具不可用。"
    "请先运行 python -m rl.cli corpus build 与 corpus index。"
)


def _store():
    """The process-wide corpus, or None when it is not built.

    Hash verification is skipped at rollout time: `load_cached` would re-hash a
    100 MB file on first use in every worker process, and the check script
    already verifies it before a run starts. The manifest hash still travels
    with every trajectory, so a drifted corpus is caught in analysis.
    """
    from harness.corpus.store import load_cached

    try:
        return load_cached(settings.harness_corpus_dir, False)
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[corpus] unavailable: %s", e)
        return None


def _require_text(value, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} 必须是字符串，收到 {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} 不能为空")
    if len(text) > limit:
        raise ValidationError(f"{field} 过长（{len(text)} 字符，上限 {limit}）")
    return text


async def corpus_search(ctx: ToolContext, query: str, k: int = DEFAULT_SEARCH_RESULTS) -> str:
    store = _store()
    if store is None:
        return CORPUS_MISSING

    text = _require_text(query, "query", MAX_QUERY_CHARS)
    if not isinstance(k, int) or isinstance(k, bool):
        raise ValidationError(f"k 必须是整数，收到 {type(k).__name__}")
    k = max(1, min(k, MAX_SEARCH_RESULTS))

    hits = store.search(text, k=k)
    if not hits:
        return f"没有检索到结果：{text}\n换一组关键词再试，或减少限定词。"

    lines = [f"检索「{text}」，{len(hits)} 条结果："]
    for rank, hit in enumerate(hits, 1):
        doc = store.get(hit.doc_id)
        if doc is None:                      # index/corpus drift; already guarded at load
            continue
        snippet = doc.text[:SNIPPET_CHARS].rstrip()
        if len(doc.text) > SNIPPET_CHARS:
            snippet += "…"
        lines.append(f"\n{rank}. [{doc.doc_id}] {doc.title}\n   {snippet}")
    lines.append("\n用 corpus_open 打开某一篇可以看到它的正文全文。")
    return "\n".join(lines)


async def corpus_open(ctx: ToolContext, doc_id: str) -> str:
    store = _store()
    if store is None:
        return CORPUS_MISSING

    did = _require_text(doc_id, "doc_id", 64)
    doc = store.get(did)
    if doc is None:
        return (
            f"语料中没有编号 {did} 的条目。"
            "编号要来自 corpus_search 的结果，不要自己拼。"
        )

    lines = [f"编号 {doc.doc_id}", f"标题 {doc.title}"]
    # Metadata is printed only here — see the module docstring. `search` must
    # never reveal it, or a question that needs two hops collapses into one.
    for key, value in sorted(doc.meta.items()):
        lines.append(f"{key} {value}")
    lines += ["", "正文", doc.text]
    return "\n".join(lines)


async def corpus_answer(ctx: ToolContext, answer: str, citations: list) -> str:
    text = _require_text(answer, "answer", MAX_ANSWER_CHARS)

    if not isinstance(citations, list):
        raise ValidationError(
            f"citations 必须是编号组成的数组，收到 {type(citations).__name__}"
        )
    if len(citations) > MAX_CITATIONS:
        raise ValidationError(f"citations 过多（{len(citations)} 条，上限 {MAX_CITATIONS}）")
    for item in citations:
        if not isinstance(item, str):
            raise ValidationError(f"citations 的每一项都要是论文编号字符串，收到 {type(item).__name__}")

    # No verdict, no scoring, no hint. The verifier reads this call out of the
    # event log afterwards; the model learns nothing from the reply.
    logger.debug("[corpus] answer len=%d citations=%d", len(text), len(citations))
    return ANSWER_ACK


HANDLERS = {
    "corpus_search": corpus_search,
    "corpus_open": corpus_open,
    "corpus_answer": corpus_answer,
}
