"""Compare GRPO checkpoints against an SFT baseline on the same tasks."""

from __future__ import annotations

import argparse
import json
import os
import random
from itertools import combinations
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_rows(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    by_id = {row["task_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError(f"duplicate task IDs in {path}")
    return by_id


def correct(row: dict) -> bool:
    return not row.get("error") and bool((row.get("reward") or {}).get("correct"))


def summarize(rows: dict[str, dict], ids: list[str]) -> dict:
    selected = [rows[task_id] for task_id in ids]
    valid = [row for row in selected if not row.get("error")]
    return {
        "n": len(selected),
        "correct": sum(correct(row) for row in selected),
        "format_valid": sum(bool((row.get("reward") or {}).get("format_valid")) for row in valid),
        "errors": len(selected) - len(valid),
        "gave_up": sum((row.get("reward") or {}).get("ended_by") in ("no-tool-call", "max-steps")
                       for row in valid),
    }


def paired(baseline: dict[str, dict], candidate: dict[str, dict], ids: list[str]) -> dict:
    differences = [int(correct(candidate[task_id])) - int(correct(baseline[task_id]))
                   for task_id in ids]
    rng = random.Random(0)
    n = len(differences)
    bootstrap = sorted(sum(rng.choices(differences, k=n)) / n for _ in range(5000))
    return {
        "wins": differences.count(1),
        "losses": differences.count(-1),
        "delta": sum(differences) / n,
        "ci95": [bootstrap[124], bootstrap[4874]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", "--dev", dest="tasks", type=Path, required=True)
    parser.add_argument("--split-name", choices=("dev", "test"), default="dev")
    parser.add_argument("--periodic-n", type=int, default=None,
                        help="Size of the GRPO periodic-evaluation subset; defaults to 100 for dev, 0 for test")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", action="append", required=True, metavar="LABEL=PATH")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines() if line.strip()]
    all_ids = [task["task_id"] for task in tasks]
    periodic_n = args.periodic_n if args.periodic_n is not None else (100 if args.split_name == "dev" else 0)
    if not 0 <= periodic_n < len(tasks):
        parser.error("--periodic-n must be between 0 and the split size - 1")
    groups = {f"全部 {len(tasks)} 题": all_ids}
    if periodic_n:
        periodic_ids = {task["task_id"] for task in random.Random(0).sample(tasks, periodic_n)}
        groups[f"周期评测 {periodic_n} 题"] = [task_id for task_id in all_ids if task_id in periodic_ids]
        groups[f"其余 {len(tasks) - periodic_n} 题"] = [
            task_id for task_id in all_ids if task_id not in periodic_ids
        ]
    paths = {"SFT": args.baseline}
    for spec in args.candidate:
        label, separator, path = spec.partition("=")
        if not separator or not label or label in paths:
            parser.error(f"invalid or duplicate --candidate: {spec}")
        paths[label] = Path(path)

    models = {label: read_rows(path) for label, path in paths.items()}
    expected = set(all_ids)
    baseline_stamp = None
    for label, rows in models.items():
        if set(rows) != expected:
            raise ValueError(f"{label}: expected {len(expected)} {args.split_name} tasks, found {len(rows)}")
        stamps = {json.dumps({k: v for k, v in row["stamp"].items() if k != "model"}, sort_keys=True)
                  for row in rows.values()}
        if len(stamps) != 1:
            raise ValueError(f"{label}: mixed environment stamps")
        stamp = stamps.pop()
        if baseline_stamp is None:
            baseline_stamp = stamp
        elif stamp != baseline_stamp:
            raise ValueError(f"{label}: environment stamp differs from SFT")

    results = {
        group: {
            label: summarize(rows, ids) for label, rows in models.items()
        } for group, ids in groups.items()
    }
    comparisons = {
        group: {
            label: paired(models["SFT"], models[label], ids)
            for label in models if label != "SFT"
        } for group, ids in groups.items()
    }
    candidate_pairs = {
        group: {
            f"{second} 对 {first}": paired(models[first], models[second], ids)
            for first, second in combinations((label for label in models if label != "SFT"), 2)
        } for group, ids in groups.items()
    }
    lines = [
        f"# Qwen3-8B HotpotQA {args.split_name}：SFT 与 GRPO 检查点对照",
        "",
        f"相同 {args.split_name} 题目与环境指纹；temperature=0。失败轨迹在正确率和格式率中按失败计。",
        "",
    ]
    if periodic_n:
        lines.insert(3, f"周期评测的 {periodic_n} 题曾用于观察 GRPO，另外 "
                        f"{len(tasks) - periodic_n} 题未用于 GRPO 周期评测。")
    for group, summary in results.items():
        lines += [f"## {group}", "", "| 模型 | 答对/总数 | 正确率 | 格式合法/总数 | 错误轨迹 | 放弃作答 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for label, values in summary.items():
            n = values["n"]
            lines.append(f"| {label} | {values['correct']}/{n} | {values['correct']/n:.1%} | "
                         f"{values['format_valid']}/{n} | {values['errors']} | {values['gave_up']} |")
        lines += ["", "| GRPO 对 SFT | 逐题赢 | 逐题输 | 净增正确率 | 配对 bootstrap 95% 区间 |",
                  "|---|---:|---:|---:|---:|"]
        for label, values in comparisons[group].items():
            low, high = values["ci95"]
            lines.append(f"| {label} | {values['wins']} | {values['losses']} | "
                         f"{values['delta']:+.1%} | [{low:+.1%}, {high:+.1%}] |")
        lines.append("")
        if candidate_pairs[group]:
            lines += ["| GRPO 检查点对照 | 逐题赢 | 逐题输 | 净增正确率 | 配对 bootstrap 95% 区间 |",
                      "|---|---:|---:|---:|---:|"]
            for label, values in candidate_pairs[group].items():
                low, high = values["ci95"]
                lines.append(f"| {label} | {values['wins']} | {values['losses']} | "
                             f"{values['delta']:+.1%} | [{low:+.1%}, {high:+.1%}] |")
            lines.append("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines).rstrip() + "\n")
    args.out.with_suffix(".json").write_text(json.dumps({
        "paths": {label: os.path.relpath(path.resolve(), REPO_ROOT) for label, path in paths.items()},
        "summaries": results, "paired": comparisons, "candidate_pairs": candidate_pairs,
    }, ensure_ascii=False, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
