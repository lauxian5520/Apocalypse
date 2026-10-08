"""SFT entry point: teacher rollouts → verifier filter → LoRA fine-tune → publish.

The cold start. Two phases, either of which can be run alone:

    # 1. collect: sample the teacher through the real environment
    python -m rl.train.run_sft collect --split train -n 400 -G 4 \\
        --model deepseek-chat --out rl/data/sft/teacher.jsonl

    # 2. train: filter by the verifier and fine-tune (needs a GPU)
    python -m rl.train.run_sft train --trajectories rl/data/sft/teacher.jsonl \\
        --base Qwen/Qwen2.5-1.5B-Instruct --out rl/data/adapters/sft

Split in two because the phases have different requirements: collection needs a
provider and no GPU, training needs a GPU and no network. Running them together
would mean a card sitting idle for the hours collection takes.

The filter is `sft.select`, which is the *same* verifier GRPO uses as its
reward. That is deliberate: the imitation target is by construction the
distribution the reward function likes, so the two phases cannot be optimising
different things.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import logging
import os
import shutil
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rl.model_storage import configure_hf_home

configure_hf_home()
os.environ.setdefault("HARNESS_CORPUS_ENABLED", "true")

logger = logging.getLogger(__name__)


def collect(args) -> int:
    """Roll out a teacher through the real environment and store trajectories."""
    from rl.train import preflight

    # Collection needs a provider and a tokenizer, never a GPU — checking for
    # torch here would refuse a phase that does not use it.
    preflight.require_rollout()

    from harness.corpus.store import load_cached
    from harness.tools.registry import ToolRegistry
    from rl.env.adapter import EvalAdapter
    from rl.env.build import PRESET, corpus_dir
    from rl.rollout import engine
    from rl.rollout.trajectory import EnvStamp, read_jsonl, write_jsonl
    from rl.tasks import split as split_mod

    tasks = split_mod.load(args.splits_dir, args.split)
    if args.n:
        import random
        tasks = random.Random(args.seed).sample(tasks, min(args.n, len(tasks)))

    store = load_cached(corpus_dir(), False)
    llm = (EvalAdapter.for_vllm(args.base_url, args.model, temperature=args.temperature)
           if args.base_url else
           EvalAdapter.from_settings(args.model, temperature=args.temperature))
    registry = ToolRegistry(PRESET)
    from rl.env.build import prompt_sha256
    stamp = EnvStamp(
        corpus_sha256=store.manifest.docs_sha256, corpus_docs=len(store),
        preset=PRESET, max_steps=registry.max_steps, model=llm.model,
        prompt_sha256=prompt_sha256(registry),
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    chunk_dir = args.out + ".chunks"
    run_config = {
        "split": args.split, "task_ids": [task["task_id"] for task in tasks],
        "group_size": args.group_size, "temperature": args.temperature,
        "chunk_size": args.chunk_size, "endpoint_sha256": hashlib.sha256(llm.url.encode()).hexdigest(),
        "stamp": stamp.to_dict(),
    }
    config_path = os.path.join(chunk_dir, "run.json")
    if args.resume:
        if not os.path.isdir(chunk_dir):
            raise SystemExit(f"续跑目录不存在：{chunk_dir}")
        with open(config_path, encoding="utf-8") as f:
            if json.load(f) != run_config:
                raise SystemExit(f"续跑参数或环境与原采集不符：{config_path}")
    else:
        if os.path.exists(args.out) or os.path.exists(chunk_dir):
            raise SystemExit(f"输出已存在：{args.out}；若要续跑，请添加 --resume")
        os.makedirs(chunk_dir)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(run_config, f, ensure_ascii=False, indent=2)

    batches = [tasks[i:i + args.chunk_size] for i in range(0, len(tasks), args.chunk_size)]
    stamp_dict = stamp.to_dict()
    total = successful = correct = 0
    print(f"采样 {len(tasks)} 题 × G={args.group_size} · 模型 {llm.model} · 并发 {args.concurrency}"
          f" · 每批 {args.chunk_size} 题 · 共 {len(batches)} 批", flush=True)
    for index, batch in enumerate(batches):
        path = os.path.join(chunk_dir, f"{index:05d}.jsonl")
        if os.path.exists(path):
            trajectories = read_jsonl(path)
            expected = Counter({task["task_id"]: args.group_size for task in batch})
            actual = Counter(t.task_id for t in trajectories)
            if actual != expected or any(t.stamp != stamp_dict for t in trajectories):
                raise SystemExit(f"续跑批次与当前任务/环境不符：{path}")
        else:
            for attempt in range(args.batch_retries + 1):
                trajectories, _ = asyncio.run(engine.run_many(
                    batch, engine.shared(llm), stamp, concurrency=args.concurrency,
                    group_size=args.group_size, schedule="refill", progress_every=0,
                ))
                if any(t.ok for t in trajectories):
                    break
                if attempt == args.batch_retries:
                    raise SystemExit(f"整批 {index + 1} 连续 {attempt + 1} 次全部失败，"
                                     "停止采集；修复 provider 后用 --resume 重试")
                delay = min(15 * 2 ** attempt, 120)
                print(f"  第 {index + 1} 批全部失败，{delay}s 后重试"
                      f"（{attempt + 1}/{args.batch_retries}）", flush=True)
                time.sleep(delay)
            temporary = path + ".tmp"
            write_jsonl(temporary, trajectories)
            os.replace(temporary, path)
        total += len(trajectories)
        successful += sum(t.ok for t in trajectories)
        correct += sum(t.ok and bool(t.reward.get("correct")) for t in trajectories)
        print(f"  {index + 1}/{len(batches)} 批 · {total} 条已落盘 · "
              f"成功 {successful} · 答对 {correct}", flush=True)

    temporary = args.out + ".tmp"
    with open(temporary, "wb") as merged:
        for index in range(len(batches)):
            with open(os.path.join(chunk_dir, f"{index:05d}.jsonl"), "rb") as part:
                shutil.copyfileobj(part, merged)
    os.replace(temporary, args.out)
    print(f"完成 {successful}/{total} 条 · 通过验证器 {correct}"
          f"（{correct / max(successful, 1):.1%}）")
    print(f"写入 {args.out}")

    if correct == 0:
        print("\n⚠ 一条都没通过验证器。教师模型太弱、或者环境/奖励有问题，"
              "先查这个再去训练——SFT 数据为空的话后面全是空转。")
        return 1
    return 0


def train(args) -> int:
    """Filter by the verifier and fine-tune a LoRA adapter."""
    from rl.train import preflight, distributed

    preflight.require_training()
    distributed.validate_gpu_layout()
    accelerator = distributed.create_accelerator()
    main_rank = distributed.is_main(accelerator)
    train_device = str(accelerator.device) if accelerator else args.device
    device_info = preflight.describe_device(train_device)
    if main_rank:
        print(f"设备：{device_info}")

    from rl.rollout.trajectory import read_jsonl
    from rl.train import sft
    from rl.train.policy_model import ModelConfig, PolicyModel
    from rl.env import template

    template.configure(args.base, args.template_family)

    trajectories = read_jsonl(args.trajectories)
    kept, funnel = sft.select(trajectories, sft.SFTConfig(
        max_per_question=args.max_per_question, min_reward=args.min_reward))
    if main_rank:
        print(sft.render_funnel(funnel))
    if not kept:
        if main_rank:
            print("\n没有可用的 SFT 数据。")
        return 1

    dataset = sft.build_dataset(kept)
    trainable = sum(sum(s.mask) for s in dataset)
    if main_rank:
        print(f"\n分词后 {len(dataset)} 条 · 可训练 {trainable} token "
              f"（占 {trainable / max(sum(len(s) for s in dataset), 1):.1%}）")

    model = PolicyModel(ModelConfig(
        model_name=args.base, learning_rate=args.lr, device=train_device,
        micro_batch_size=args.micro_batch_size,
    ), accelerator=accelerator, adapter=args.adapter)

    import random
    rng = random.Random(args.seed)
    step = 0
    for epoch in range(args.epochs):
        order = list(dataset)
        rng.shuffle(order)
        for start in range(0, len(order), args.batch_size):
            batch = order[start:start + args.batch_size]
            step += 1
            if step <= args.resume_step:
                continue
            stats = model.sft_step(batch)
            if step % args.log_every == 0 and main_rank:
                print(f"  epoch {epoch} step {step:4}  loss {stats['loss']:.4f}  "
                      f"grad {stats['grad_norm']:.3f}  tokens {stats['trainable_tokens']}")
            if args.checkpoint_every and step % args.checkpoint_every == 0:
                checkpoint = model.save_adapter(
                    os.path.join(args.out + ".checkpoints", f"step-{step:06d}"))
                if main_rank:
                    print(f"  step {step:4} checkpoint 写入 {checkpoint}", flush=True)

    path = model.save_adapter(args.out)
    if main_rank:
        print(f"\nadapter 写入 {path}")
        print("下一步：把它作为 GRPO 的起点\n"
              f"  vllm serve {args.base} --enable-lora --lora-modules sft={path}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="冷启动 SFT")
    sub = parser.add_subparsers(dest="phase", required=True)

    c = sub.add_parser("collect", help="用教师模型采样（需要 provider，不需要 GPU）")
    c.add_argument("--split", default="train")
    c.add_argument("-n", type=int, default=400, help="抽多少题，0 表示全部")
    c.add_argument("-G", "--group-size", type=int, default=4)
    c.add_argument("--model", default="")
    c.add_argument("--base-url", default="", help="vLLM 地址；留空走配置的 provider")
    c.add_argument("--temperature", type=float, default=1.0)
    c.add_argument("--concurrency", type=int, default=8)
    c.add_argument("--chunk-size", type=int, default=25, help="每批题数；每批结束立即落盘")
    c.add_argument("--batch-retries", type=int, default=2, help="整批全部失败时退避重试次数")
    c.add_argument("--resume", action="store_true", help="跳过已完成且环境一致的批次")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--out", default=os.path.join(REPO_ROOT, "rl/data/sft/teacher.jsonl"))
    c.add_argument("--splits-dir", default=os.path.join(REPO_ROOT, "rl/data/splits"))
    c.set_defaults(func=collect)

    t = sub.add_parser("train", help="筛选并微调（需要 GPU）")
    t.add_argument("--trajectories", default=os.path.join(REPO_ROOT, "rl/data/sft/teacher.jsonl"))
    t.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    t.add_argument("--adapter", default="", help="从已有 SFT LoRA 继续训练")
    t.add_argument("--resume-step", type=int, default=0,
                   help="跳过已训练的批次；需同时提供对应步的 --adapter（优化器重新初始化）")
    t.add_argument("--checkpoint-every", type=int, default=0,
                   help="每 N 步另存一次 LoRA；0 表示仅在训练结束时保存")
    t.add_argument("--template-family", choices=("qwen2.5", "qwen3"), default=None)
    t.add_argument("--out", default=os.path.join(REPO_ROOT, "rl/data/adapters/sft"))
    t.add_argument("--epochs", type=int, default=2)
    t.add_argument("--batch-size", type=int, default=4)
    t.add_argument("--micro-batch-size", type=int, default=1)
    t.add_argument("--lr", type=float, default=1e-4)
    t.add_argument("--device", default="cuda")
    t.add_argument("--max-per-question", type=int, default=2)
    t.add_argument("--min-reward", type=float, default=1.0)
    t.add_argument("--log-every", type=int, default=10)
    t.add_argument("--seed", type=int, default=0)
    t.set_defaults(func=train)

    args = parser.parse_args()
    if args.phase == "collect" and (args.group_size < 1 or args.concurrency < 1 or args.chunk_size < 1 or args.batch_retries < 0):
        parser.error("-G、--concurrency 和 --chunk-size 必须大于 0；--batch-retries 不能小于 0")
    if args.phase == "train" and (args.resume_step < 0 or args.checkpoint_every < 0 or
                                   (args.resume_step and not args.adapter)):
        parser.error("--resume-step 和 --checkpoint-every 不能小于 0；续训须提供 --adapter")
    return args.func(args)


def _run_reporting_oom(entry) -> int:
    """Turn a CUDA OOM into the one instruction that usually fixes it.

    The OOM class lives on torch, which is imported lazily and may be absent;
    matching the name keeps this module importable without it.
    """
    try:
        return entry()
    except Exception as e:                      # noqa: BLE001 — re-raised below
        if type(e).__name__ != "OutOfMemoryError":
            raise
        from rl.train import preflight   # torch-free; safe to import here
        raise SystemExit(preflight.oom_message()) from e


if __name__ == "__main__":
    raise SystemExit(_run_reporting_oom(main))
