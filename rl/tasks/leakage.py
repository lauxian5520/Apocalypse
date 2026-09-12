"""Can a model answer these questions without searching at all?

**The single most important measurement for this environment.** HotpotQA asks
about Wikipedia entities, and every modern LLM has read Wikipedia. If a policy
can answer from parametric memory, the retrieval tools are decoration: the model
collects the outcome reward without ever using them, learns to skip them, and
every number the project reports is measuring recall of pretraining data rather
than search ability. The environment would be hollow and nothing downstream
would reveal it — the trajectories would look fine.

So: ask the model each question **closed-book**, with no tools and no context,
and score it with the same verifier a real rollout is scored with.

Two deliberate choices make this an *upper* bound on leakage, which is the
useful direction for a risk measurement:

- The prompt is written to help the model succeed — it is told to answer from
  memory and to guess rather than refuse. A prompt that encouraged "I don't
  know" would understate the risk.
- The strongest available model is used, not the 1.5B policy. A question the
  teacher knows cold is a question the policy might also know, and a question
  the teacher cannot answer is safely beyond the policy.

What to do with the number is a judgement call the result informs, not a
threshold hidden in here: the CLI prints the rate by `level` and `qtype` so a
decision can be made about which slices to drop.
"""
import random
from dataclasses import dataclass, field

from rl.verifiers import outcome as outcome_verifier

CLOSED_BOOK_SYSTEM = (
    "你在做一个封闭书本问答测试。只依靠你自己的知识回答，没有任何检索工具。"
    "即使不确定也要给出你最可能的答案，不要回答「不知道」或「无法确定」。"
)

CLOSED_BOOK_PROMPT = (
    "问题：{question}\n\n"
    "只输出答案本身，尽量短——一个名字、一个年份、一个地名，或者 yes / no。"
    "不要解释，不要写完整句子。"
)

# The answer is a span — a name, a year, yes/no — but a thinking model spends
# its budget on reasoning before emitting a visible character, and how much it
# spends varies per question. Measured: `deepseek-v4-pro` averaged 515
# completion tokens per one-word answer, and at a 1200 cap **22% of a 500-batch
# hit the ceiling and returned nothing at all**. Sizing this off the average was
# the mistake; it has to clear the tail.
#
# Raising it is close to free: billing is per token actually generated, so a
# question that finishes in 300 tokens costs the same under either cap. The only
# thing a large cap buys is not losing the slow fifth.
ANSWER_MAX_TOKENS = 4096


@dataclass
class LeakageResult:
    task_id: str
    question: str
    gold: str
    predicted: str
    level: str
    qtype: str
    exact_match: bool
    f1: float

    @property
    def answered(self) -> bool:
        """Whether this question actually got a verdict.

        An empty prediction is a *failed measurement*, not evidence that the
        model does not know the answer — the usual cause is a thinking model
        spending its whole output budget on reasoning (measured: 18% of a
        `deepseek-v4-flash` batch at a 4096 cap). Scoring those as "not leaked"
        would quietly admit leaked questions into the dataset, which is the one
        thing this filter exists to prevent. Consumers treat them as *unknown*
        and leave them out of the covered set.
        """
        return bool(self.predicted.strip())


@dataclass
class LeakageReport:
    results: list[LeakageResult] = field(default_factory=list)

    @property
    def answered(self) -> list["LeakageResult"]:
        """Only the questions that actually produced a verdict."""
        return [r for r in self.results if r.answered]

    @property
    def rate(self) -> float:
        """Fraction answerable closed-book, over questions that got a verdict.

        The denominator is deliberately `answered`, not `results`: including
        failed measurements would dilute the rate toward zero and understate
        the risk.
        """
        answered = self.answered
        if not answered:
            return 0.0
        return sum(1 for r in answered if r.exact_match) / len(answered)

    @property
    def unknown_rate(self) -> float:
        if not self.results:
            return 0.0
        return 1.0 - len(self.answered) / len(self.results)

    def by(self, attr: str) -> dict[str, tuple[int, float]]:
        """Leakage rate grouped by `level` or `qtype`: {key: (n, rate)}."""
        buckets: dict[str, list[LeakageResult]] = {}
        for r in self.answered:
            buckets.setdefault(getattr(r, attr), []).append(r)
        return {
            key: (len(rs), sum(1 for r in rs if r.exact_match) / len(rs))
            for key, rs in sorted(buckets.items())
        }

    def leaked_ids(self) -> set[str]:
        return {r.task_id for r in self.results if r.exact_match}


def run(teacher, tasks: list[dict], sample: int = 300, seed: int = 0,
        concurrency: int = 8, chunk: int = 0, on_chunk=None,
        skip_ids: set[str] | None = None) -> LeakageReport:
    """Score `sample` questions closed-book.

    A full sweep of HotpotQA's dev split is 7,405 calls against a thinking model
    — hours of wall clock. So results are produced in chunks and handed to
    `on_chunk` as they land, letting the caller append them to disk: a sweep
    interrupted at hour four keeps its first four hours of work. `skip_ids`
    lets a re-run pick up where the last one stopped.
    """
    rng = random.Random(seed)
    chosen = rng.sample(tasks, min(sample, len(tasks)))
    if skip_ids:
        chosen = [t for t in chosen if t["task_id"] not in skip_ids]

    report = LeakageReport()
    size = chunk if chunk > 0 else len(chosen)

    for start in range(0, len(chosen), size):
        batch = chosen[start:start + size]
        prompts = [CLOSED_BOOK_PROMPT.format(question=t["question"]) for t in batch]
        answers = teacher.ask_many(
            prompts,
            system=CLOSED_BOOK_SYSTEM,
            max_tokens=ANSWER_MAX_TOKENS,
            concurrency=concurrency,
        )

        fresh = []
        for task, predicted in zip(batch, answers):
            scored = outcome_verifier.score(predicted, task["answer"])
            fresh.append(LeakageResult(
                task_id=task["task_id"],
                question=task["question"],
                gold=task["answer"],
                predicted=predicted,
                level=task.get("level", ""),
                qtype=task.get("qtype", ""),
                exact_match=scored.exact_match,
                f1=scored.f1,
            ))
        report.results.extend(fresh)
        if on_chunk is not None:
            on_chunk(fresh, len(report.results), len(chosen))

    return report


def render(report: LeakageReport) -> str:
    lines = [
        f"闭卷可答率（= 泄漏率）：{report.rate:.1%}"
        f"   有效样本 {len(report.answered)}/{len(report.results)}",
    ]
    if report.unknown_rate:
        lines.append(
            f"⚠ {report.unknown_rate:.1%} 的题没拿到答案（输出预算被推理耗尽），"
            f"按「未知」处理，不计入分母、也不进入覆盖集"
        )
    lines += ["", "按难度："]
    for key, (n, rate) in report.by("level").items():
        lines.append(f"  {key:<8} n={n:<5} {rate:.1%}")
    lines.append("按题型：")
    for key, (n, rate) in report.by("qtype").items():
        lines.append(f"  {key:<12} n={n:<5} {rate:.1%}")
    return "\n".join(lines)
