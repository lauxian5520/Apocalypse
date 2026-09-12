"""The frozen research corpus the Deep Research environment is built on.

This package is the *read* side: loading a corpus that someone already froze,
verifying it against its manifest, and querying it. The *write* side — fetching
from arXiv and writing the frozen artifacts — lives in `rl/corpus/`, because
building a dataset is an offline job and the server has no business doing it.

It sits inside `harness/` rather than beside the training code because the
corpus *is* the environment: `tools/builtin/corpus.py` serves it to the agent,
and the reward is defined in terms of what it returns. One implementation, read
by the tool handler at rollout time and by the synthesis pipeline at build time,
is the only way those two can be guaranteed to agree — and an environment whose
search behaves differently in training than in evaluation is not an environment,
it is two.

Nothing here imports anything above `core/`.
"""
from harness.corpus.schema import DEFAULT_CATEGORIES, DOCS_NAME, MANIFEST_NAME, Doc
from harness.corpus.store import CorpusStore, Manifest, iter_docs, load_manifest, verify

__all__ = [
    "Doc",
    "CorpusStore",
    "Manifest",
    "DEFAULT_CATEGORIES",
    "DOCS_NAME",
    "MANIFEST_NAME",
    "iter_docs",
    "load_manifest",
    "verify",
]
