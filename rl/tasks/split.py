"""Turning 7,405 HotpotQA questions into frozen train / dev / test splits.

Two decisions here are worth more than the code.

**Leaked questions are dropped; one-query-solvable ones are only labelled.**
They fail in different ways and deserve different treatment:

- A question a model answers *closed-book* is not a retrieval task at all. Left
  in, it pays reward for reciting pretraining data, and the policy learns to
  skip the tools. It is removed from every split, including eval, because it
  measures the wrong thing everywhere.
- A question one BM25 query happens to solve is still a real retrieval task —
  the agent must form a query, read results and synthesise an answer. It is
  *easy*, not hollow. Dropping it would throw away roughly half the dataset
  (measured: 48.3%) to fix a problem the online difficulty curriculum already
  handles: an easy question becomes p=1 as the policy improves, its
  group-normalised advantage goes to zero, and it stops contributing gradient
  by itself. So it is labelled, kept, and every evaluation reports the
  multi-step subset alongside the overall number.

**Stratification is on `qtype`, not `level`.** All 7,405 dev questions are
`level=hard` — a property of HotpotQA's dev release, verified rather than
assumed. The real axis of variation is bridge (5,918) versus comparison
(1,487), and those behave differently enough that a split which drifted on
their ratio would make dev and test incomparable.

Splits are written with a content hash, for the same reason the corpus is: a
number is only meaningful against a named split.
"""
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

DEFAULT_SIZES = {"train": 5000, "dev": 1200, "test": 1205}
SPLIT_NAMES = ("train", "dev", "test")


@dataclass
class Funnel:
    """Where every question went. This table is a deliverable, not a log line."""

    total: int = 0
    dropped_leaked: int = 0
    dropped_uncovered: int = 0
    labelled_one_query: int = 0
    kept: int = 0
    sizes: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)
    filters: list = field(default_factory=list)

    def render(self) -> str:
        lines = ["任务漏斗", f"  原始题目                {self.total}"]
        if self.filters:
            lines.append(f"  泄漏过滤器              {' ∪ '.join(self.filters)}")
        lines.append(
            f"  − 闭卷可答（泄漏）丢弃   {self.dropped_leaked}"
            f"  ({self.dropped_leaked / max(self.total, 1):.1%})"
        )
        if self.dropped_uncovered:
            lines.append(
                f"  − 覆盖不全丢弃           {self.dropped_uncovered}"
                f"  ({self.dropped_uncovered / max(self.total, 1):.1%})"
                f"  —— 未经全部过滤器复核，保留会让过滤强度不一致"
            )
        lines += [
            f"  = 保留                  {self.kept}",
            f"    其中「一次检索即可解」  {self.labelled_one_query}"
            f"  ({self.labelled_one_query / max(self.kept, 1):.1%})  —— 只打标签，不丢弃",
            "",
            "切分",
        ]
        for name in SPLIT_NAMES:
            n = self.sizes.get(name, 0)
            types = self.by_type.get(name, {})
            detail = " · ".join(f"{k}={v}" for k, v in sorted(types.items()))
            lines.append(f"  {name:<6} {n:<6} {detail}")
        return "\n".join(lines)


def stratified_split(
    tasks: list[dict],
    sizes: dict | None = None,
    seed: int = 0,
) -> dict[str, list[dict]]:
    """Split while preserving the bridge/comparison ratio in each part."""
    sizes = sizes or DEFAULT_SIZES
    rng = random.Random(seed)

    buckets: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        buckets[task.get("qtype", "")].append(task)
    for group in buckets.values():
        rng.shuffle(group)

    total_wanted = sum(sizes.values())
    available = sum(len(g) for g in buckets.values())
    if total_wanted > available:
        # The leakage filter removes ~40% of HotpotQA's dev split (measured),
        # so the nominal sizes routinely will not fit. Scaling the whole split
        # down proportionally keeps the train:dev:test ratio the caller asked
        # for, which is what actually matters; refusing outright would just
        # make every caller recompute the same fractions by hand.
        scale = available / total_wanted
        sizes = {name: int(n * scale) for name, n in sizes.items()}
        # Give the remainder to train — dev and test only need to be big enough
        # to measure, while train wants everything left over.
        sizes["train"] += available - sum(sizes.values())

    out: dict[str, list[dict]] = {name: [] for name in SPLIT_NAMES}
    cursors = {k: 0 for k in buckets}
    for name in SPLIT_NAMES:
        want = sizes[name]
        for qtype, group in buckets.items():
            # Proportional share of this split, by the type's overall frequency.
            take = round(want * len(group) / available)
            chunk = group[cursors[qtype]: cursors[qtype] + take]
            cursors[qtype] += len(chunk)
            out[name].extend(chunk)
        rng.shuffle(out[name])

    # Rounding can leave the last split a few short; top it up from whatever
    # remains rather than silently returning an off-size split.
    leftover = [t for qtype, group in buckets.items() for t in group[cursors[qtype]:]]
    rng.shuffle(leftover)
    for name in SPLIT_NAMES:
        while len(out[name]) < sizes[name] and leftover:
            out[name].append(leftover.pop())

    return out


def build(
    tasks: list[dict],
    leaked_ids: set[str],
    one_query_ids: set[str],
    sizes: dict | None = None,
    seed: int = 0,
    covered_ids: set[str] | None = None,
    filters: list[str] | None = None,
) -> tuple[dict[str, list[dict]], Funnel]:
    """Freeze splits from the surviving questions.

    `covered_ids`, when given, is the set every leakage filter actually
    evaluated. Questions outside it are dropped rather than kept, because a
    dataset where some questions cleared two closed-book models and others
    cleared only one has non-uniform filtering strength — and the weakly
    filtered half would quietly inflate any number measured on it. Losing
    questions is the cheaper problem.
    """
    funnel = Funnel(total=len(tasks), filters=list(filters or []))

    kept = []
    for task in tasks:
        if task["task_id"] in leaked_ids:
            funnel.dropped_leaked += 1
            continue
        if covered_ids is not None and task["task_id"] not in covered_ids:
            funnel.dropped_uncovered += 1
            continue
        enriched = dict(task)
        one_query = task["task_id"] in one_query_ids
        enriched["one_query_solvable"] = one_query
        funnel.labelled_one_query += one_query
        kept.append(enriched)

    funnel.kept = len(kept)
    splits = stratified_split(kept, sizes, seed)
    funnel.sizes = {name: len(rows) for name, rows in splits.items()}
    funnel.by_type = {
        name: dict(Counter(t.get("qtype", "") for t in rows))
        for name, rows in splits.items()
    }
    return splits, funnel


def write(out_dir: str, splits: dict[str, list[dict]]) -> dict[str, str]:
    """Write each split as JSONL and return its sha256."""
    os.makedirs(out_dir, exist_ok=True)
    hashes = {}
    for name, rows in splits.items():
        path = os.path.join(out_dir, f"{name}.jsonl")
        payload = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        hashes[name] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with open(os.path.join(out_dir, "splits.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"sha256": hashes, "sizes": {k: len(v) for k, v in splits.items()}},
            f, ensure_ascii=False, indent=2, sort_keys=True,
        )
    return hashes


def load(out_dir: str, name: str) -> list[dict]:
    path = os.path.join(out_dir, f"{name}.jsonl")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"切分不存在：{path}（先运行 python -m rl.cli tasks split）")
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
