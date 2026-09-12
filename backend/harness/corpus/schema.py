"""The frozen document record, and the names of a corpus's files on disk.

One record per retrievable passage, deliberately source-agnostic: the same
schema carries a Wikipedia paragraph and an arXiv abstract, so the environment
tools, the index and every downstream verifier are unaware of which corpus they
are running against.

The field split is the design:

- **`title` and `text` are the searchable half.** `searchable()` is what the
  BM25 index is built over and the only thing `corpus_search` reveals.
- **`meta` is the structured half**, shown only by `corpus_open`.

Keeping `meta` out of the index is not a detail. Indexing a rare proper noun —
an author, a date, a category — lets one BM25 query answer a question that was
supposed to take two hops, which measurably collapses the task. `meta` is a
flat `dict[str, str]` rather than typed fields because what counts as metadata
is a property of the source, and the tools only ever print it.
"""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Doc:
    """One retrievable passage."""

    doc_id: str
    title: str
    text: str
    meta: dict[str, str] = field(default_factory=dict)

    def searchable(self) -> str:
        """Exactly what the BM25 index is built over.

        `meta` is absent by design — see the module docstring. Changing this
        function changes what the environment *is*, so it is one function
        rather than an expression inlined at both the index and query sites.
        """
        return f"{self.title}\n\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Doc":
        # Unknown keys are dropped rather than raising: a corpus written by a
        # newer build should still load in an older checkout, the same way the
        # session log tolerates unknown ignorable events.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


# arXiv categories a build considers, when the source is arXiv. Kept here so a
# manifest can record it and a rebuild is reproducible from the manifest alone.
DEFAULT_CATEGORIES = ("cs.AI", "cs.CL", "cs.CV", "cs.LG")

MANIFEST_NAME = "manifest.json"
DOCS_NAME = "docs.jsonl"
TASKS_NAME = "tasks.jsonl"
