"""Evaluate the base/SFT policies or named LoRAs with the GRPO rollout path."""

import argparse
import asyncio
import json
import hashlib
import logging
import os
import random
import re
from pathlib import Path

from rl.model_storage import configure_hf_home

configure_hf_home()
os.environ.setdefault("HARNESS_CORPUS_ENABLED", "true")


def summarize(trajectories, expected: int) -> dict:
    ids = [t.task_id for t in trajectories]
    if len(ids) != len(set(ids)) or len(ids) > expected:
        raise ValueError("重复任务或轨迹数超过选定任务数")
    ok = [t for t in trajectories if t.ok]
    n = len(ok)
    def count(key):
        return sum(bool(t.reward.get(key)) for t in ok)
    def fraction(key):
        return round(count(key) / expected, 4) if expected else 0.0
    return {
        "status": "complete" if len(trajectories) == expected else "incomplete",
        "completed": len(trajectories), "expected": expected,
        "missing": expected - len(trajectories), "valid": n,
        "errors": len(trajectories) - n,
        "pass@1": fraction("correct"), "exact_match": fraction("exact_match"),
        "format_valid": fraction("format_valid"), "voided": fraction("voided"),
        "gave_up": round(sum(t.reward.get("ended_by") in ("no-tool-call", "max-steps") for t in ok) / expected, 4) if expected else 0.0,
        "valid_only": {
            "pass@1": round(count("correct") / n, 4) if n else 0.0,
            "format_valid": round(count("format_valid") / n, 4) if n else 0.0,
            "mean_f1": round(sum(t.reward.get("f1") or 0 for t in ok) / n, 4) if n else 0.0,
            "mean_tool_calls": round(sum(t.reward.get("tool_calls") or 0 for t in ok) / n, 2) if n else 0.0,
        },
    }


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_resume(path, receipt, saved):
    manifest = path.with_suffix(".run.json")
    if path.exists() and not manifest.exists():
        raise ValueError("旧评测缺少运行清单，禁止续写；请使用新输出目录")
    if manifest.exists() and json.loads(manifest.read_text()) != receipt:
        raise ValueError("评测任务、生成参数、环境或 adapter 已变化，禁止混用")
    ids = [t.task_id for t in saved]
    if len(ids) != len(set(ids)):
        raise ValueError("已有评测包含重复任务 ID")
    if not manifest.exists():
        manifest.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="Qwen/Qwen3-8B")
    adapters = parser.add_mutually_exclusive_group(required=True)
    adapters.add_argument("--adapter", help="SFT LoRA; evaluates base and SFT")
    adapters.add_argument("--candidate", action="append", metavar="LABEL=PATH",
                          help="Evaluate a named LoRA; repeat for multiple checkpoints")
    parser.add_argument("--vllm", default="http://127.0.0.1:8000")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--n", type=int, default=100, help="0 evaluates the full selected split")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()
    if args.n < 0 or args.concurrency < 1 or args.batch < 1 or args.max_tokens < 1:
        parser.error("n 不能为负；concurrency、batch、max-tokens 必须为正数")
    if args.candidate:
        variants = []
        labels = set()
        for spec in args.candidate:
            label, separator, path = spec.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", label) or label in labels:
                parser.error("--candidate 必须是唯一的 LABEL=PATH，LABEL 只含字母、数字、_、-")
            adapter_path = Path(path).resolve()
            if not all((adapter_path / name).is_file() for name in (
                "adapter_model.safetensors", "adapter_config.json"
            )):
                parser.error(f"LoRA 文件不完整：{adapter_path}")
            labels.add(label)
            variants.append((label, f"{label}-eval", str(adapter_path)))
    else:
        variants = [("base", args.base, None), ("sft", "sft-eval", args.adapter)]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from harness.corpus.store import load_cached
    from harness.tools.registry import ToolRegistry
    from rl.env import template
    from rl.env.build import PRESET, corpus_dir, prompt_sha256
    from rl.env.policy_adapter import PolicyAdapter
    from rl.eval.report import render
    from rl.rollout import engine
    from rl.rollout.trajectory import EnvStamp, Trajectory
    from rl.tasks import split as split_mod
    from rl.train.lora_serve import VLLMServer

    template.configure(args.base)
    store = load_cached(corpus_dir(), False)
    registry = ToolRegistry(PRESET)
    tasks = split_mod.load("rl/data/splits", args.split)
    chosen = random.Random(args.seed).sample(tasks, min(args.n, len(tasks))) if args.n else tasks
    tasks_by_id = {task["task_id"]: task for task in chosen}
    selected = set(tasks_by_id)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    server = VLLMServer(args.vllm)
    served = server.wait_ready()
    if args.base not in served:
        raise RuntimeError(f"vLLM 提供 {served}，预期 {args.base}")
    server.check_runtime_lora()
    logging.info("%s 题数 %d；语料 %d 段；服务 %s", args.split, len(chosen), len(store), served)

    for label, model, adapter_path in variants:
        if adapter_path:
            server.load(model, adapter_path)
        stamp = EnvStamp(
            corpus_sha256=store.manifest.docs_sha256, corpus_docs=len(store),
            preset=PRESET, max_steps=registry.max_steps, model=model,
            template_sha256=template.template_sha256(),
            prompt_sha256=prompt_sha256(registry),
        )
        path = args.out_dir / f"{label}.jsonl"
        saved = []
        if path.exists():
            saved = [Trajectory.from_dict(json.loads(line)) for line in path.read_text().splitlines()
                     if line.strip()]
            if any(t.task_id not in selected or t.stamp != stamp.to_dict() for t in saved):
                raise RuntimeError(f"{path} 的任务或环境指纹与本次评测不同")
        receipt = {
            "format": 1, "base": args.base, "split": args.split,
            "split_sha256": file_hash(Path("rl/data/splits") / f"{args.split}.jsonl"),
            "selected_ids": [t["task_id"] for t in chosen], "seed": args.seed,
            "environment": stamp.to_dict(),
            "generation": {"max_tokens": args.max_tokens, "temperature": 0.0, "top_p": 0.95},
            "adapter": {name: file_hash(Path(adapter_path) / name) for name in
                        ("adapter_model.safetensors", "adapter_config.json")} if adapter_path else None,
        }
        check_resume(path, receipt, saved)
        done = {t.task_id for t in saved}
        remaining = [task for task in chosen if task["task_id"] not in done]
        logging.info("%s 已有 %d/%d 题，继续 %d 题", label, len(saved), len(chosen), len(remaining))
        with path.open("a", encoding="utf-8") as output:
            for start in range(0, len(remaining), args.batch):
                batch = remaining[start:start + args.batch]
                def policy():
                    return PolicyAdapter(args.vllm, model, max_tokens=args.max_tokens,
                                         temperature=0.0, top_p=0.95)
                trajectories, _ = asyncio.run(engine.run_many(
                    batch, policy, stamp, concurrency=args.concurrency,
                    group_size=1, schedule="refill", progress_every=0,
                ))
                for trajectory in trajectories:
                    trajectory.sampled = []  # Evaluation needs events and rewards, not token logprobs.
                    output.write(json.dumps(trajectory.to_dict(), ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
                saved.extend(trajectories)
                summary = summarize(saved, len(chosen))
                (args.out_dir / f"{label}.summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
                logging.info("%s %d/%d pass@1=%.1f%% format=%.1f%% errors=%d",
                             label, len(saved), len(chosen), 100 * summary["pass@1"],
                             100 * summary["format_valid"], summary["errors"])
        summary = summarize(saved, len(chosen))
        (args.out_dir / f"{label}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        report = render(saved, tasks_by_id, expected=len(chosen), title=f"Qwen3-8B {label} · HotpotQA {args.split}")
        (args.out_dir / f"{label}.report.md").write_text(report)
    logging.info("评测完成：%s", args.out_dir)


if __name__ == "__main__":
    main()
