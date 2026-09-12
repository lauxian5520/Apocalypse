"""Turning a set of trajectories into the tables a reader actually wants.

Three audiences, three sections, and the order matters:

1. **Did it get the answers right** — pass@1, EM, F1, and the split between
   "wrong" and "never answered", which are different failures with different
   fixes.
2. **Did it earn them** — grounding against the human-annotated supporting
   facts, and how often the anti-gaming rules voided a trajectory. A pass@1
   without this number beside it is not trustworthy, because the cheapest way
   to raise pass@1 is to stop checking.
3. **What it cost** — wall clock, its decomposition, and the long tail. This is
   the section an RL-systems reader goes to first.

Every table is broken out by `one_query_solvable`, because roughly half of
HotpotQA is answerable from a single retrieval and an aggregate over both
halves hides which one a change actually moved.

Markdown, because it renders in the README, in a PR, and in a terminal, and
because the numbers should be copy-pasteable into a writeup without a plotting
dependency on a Raspberry Pi.
"""
from collections import defaultdict
from dataclasses import dataclass

from rl.rollout.trajectory import Trajectory


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _mean(rows: list[dict], key: str) -> float:
    if not rows:
        return 0.0
    return sum(r.get(key) or 0 for r in rows) / len(rows)


def _frac(rows: list[dict], pred) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if pred(r)) / len(rows)


@dataclass
class Slice:
    """One group of trajectories and the metrics over it."""

    name: str
    rows: list[dict]

    @property
    def n(self) -> int:
        return len(self.rows)

    def metrics(self) -> dict:
        r = self.rows
        return {
            "n": len(r),
            "pass@1": _frac(r, lambda x: x.get("correct")),
            "exact_match": _frac(r, lambda x: x.get("exact_match")),
            "f1": _mean(r, "f1"),
            "format_valid": _frac(r, lambda x: x.get("format_valid")),
            "voided": _frac(r, lambda x: x.get("voided")),
            "gave_up": _frac(r, lambda x: x.get("ended_by") in ("no-tool-call", "max-steps")),
            "grounding_f1": _mean(r, "grounding_f1"),
            "steps": _mean(r, "steps"),
            "tool_calls": _mean(r, "tool_calls"),
            "redundant": _mean(r, "redundant_query_fraction"),
        }


def slices(trajectories: list[Trajectory], tasks_by_id: dict[str, dict]) -> list[Slice]:
    """Overall, plus the splits that change how a number should be read."""
    ok = [t for t in trajectories if t.ok]
    rows = [t.reward for t in ok]

    by_difficulty: dict[str, list[dict]] = defaultdict(list)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for t in ok:
        task = tasks_by_id.get(t.task_id) or {}
        label = "一次检索可解" if task.get("one_query_solvable") else "需多步检索"
        by_difficulty[label].append(t.reward)
        by_type[task.get("qtype") or "?"].append(t.reward)

    out = [Slice("全部", rows)]
    out += [Slice(k, v) for k, v in sorted(by_difficulty.items())]
    out += [Slice(k, v) for k, v in sorted(by_type.items())]
    return out


def render(
    trajectories: list[Trajectory],
    tasks_by_id: dict[str, dict],
    stats=None,
    title: str = "评测报告",
) -> str:
    ok = [t for t in trajectories if t.ok]
    failed = len(trajectories) - len(ok)
    stamp = ok[0].stamp if ok else {}

    lines = [f"# {title}", ""]

    # The environment a number was produced under is part of the number.
    if stamp:
        lines += [
            "| 环境 | |",
            "|---|---|",
            f"| 模型 | `{stamp.get('model', '?')}` |",
            f"| 预设 | `{stamp.get('preset', '?')}` · max_steps {stamp.get('max_steps', '?')} |",
            f"| 语料 | {stamp.get('corpus_docs', '?')} 段 · `{str(stamp.get('corpus_sha256', ''))[:16]}…` |",
            "",
        ]
    if failed:
        lines += [f"> ⚠ {failed} 条轨迹因错误未完成，未计入下表。", ""]

    lines += ["## 结果", "",
              "| 切片 | n | pass@1 | EM | F1 | 格式合法 | 作废 | 放弃 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in slices(trajectories, tasks_by_id):
        m = s.metrics()
        lines.append(
            f"| {s.name} | {m['n']} | **{_pct(m['pass@1'])}** | {_pct(m['exact_match'])} | "
            f"{m['f1']:.3f} | {_pct(m['format_valid'])} | {_pct(m['voided'])} | {_pct(m['gave_up'])} |"
        )

    lines += ["", "## 过程", "",
              "| 切片 | 引用扎实度 F1 | 平均步数 | 平均工具调用 | 重复检索 |",
              "|---|---:|---:|---:|---:|"]
    for s in slices(trajectories, tasks_by_id):
        m = s.metrics()
        lines.append(
            f"| {s.name} | {m['grounding_f1']:.3f} | {m['steps']:.1f} | "
            f"{m['tool_calls']:.1f} | {_pct(m['redundant'])} |"
        )

    if stats is not None:
        d = stats.to_dict()
        lines += ["", "## 吞吐", "", "| 指标 | 值 |", "|---|---:|"]
        labels = {
            "episodes": "轨迹数", "schedule": "调度", "concurrency": "并发",
            "wall_seconds": "墙钟（秒）", "throughput_per_min": "轨迹/分钟",
            "p50_seconds": "p50（秒）", "p95_seconds": "p95（秒）", "p99_seconds": "p99（秒）",
            "slowest_5pct_share": "最慢 5% 占总时长", "utilisation": "并发占用率",
        }
        for key, label in labels.items():
            if key in d:
                lines.append(f"| {label} | {d[key]} |")

    lines += ["", "## 作弊审计", ""]
    voided = [t for t in ok if t.reward.get("voided")]
    if not voided:
        lines.append("本次运行没有轨迹被反作弊规则作废。")
    else:
        lines.append(f"{len(voided)} 条被作废（{_pct(len(voided) / max(len(ok), 1))}）：")
        lines.append("")
        reasons: dict[str, int] = defaultdict(int)
        for t in voided:
            for note in t.reward.get("notes") or []:
                reasons[note.split("（")[0].split(" ")[0]] += 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason} × {count}")
        lines += ["", "示例：", ""]
        for t in voided[:3]:
            lines.append(f"- **{t.question[:80]}**")
            lines.append(f"  - 答案 `{t.reward.get('predicted', '')[:40]}` / 正解 `{t.gold_answer[:40]}`")
            for note in (t.reward.get("notes") or [])[:2]:
                lines.append(f"  - {note}")

    return "\n".join(lines) + "\n"
