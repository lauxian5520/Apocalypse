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
import logging
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
    from rl.rollout.trajectory import EnvStamp, write_jsonl
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
    stamp = EnvStamp(
        corpus_sha256=store.manifest.docs_sha256, corpus_docs=len(store),
        preset=PRESET, max_steps=registry.max_steps, model=llm.model,
    )

    print(f"采样 {len(tasks)} 题 × G={args.group_size} · 模型 {llm.model} · 并发 {args.concurrency}")
    trajectories, stats = asyncio.run(engine.run_many(
        tasks, engine.shared(llm), stamp, concurrency=args.concurrency,
        group_size=args.group_size, schedule="refill",
    ))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    write_jsonl(args.out, trajectories)
    ok = [t for t in trajectories if t.ok]
    correct = sum(1 for t in ok if t.reward.get("correct"))
    print(f"完成 {len(ok)}/{len(trajectories)} 条 · 通过验证器 {correct}"
          f"（{correct / max(len(ok), 1):.1%}）· {stats.to_dict()['wall_seconds']}s")
    print(f"写入 {args.out}")

    if correct == 0:
        print("\n⚠ 一条都没通过验证器。教师模型太弱、或者环境/奖励有问题，"
              "先查这个再去训练——SFT 数据为空的话后面全是空转。")
        return 1
    return 0


def train(args) -> int:
    """Filter by the verifier and fine-tune a LoRA adapter."""
    from rl.train import preflight

    preflight.require_training()
    print(f"设备：{preflight.describe_device(args.device)}")

    from rl.rollout.trajectory import read_jsonl
    from rl.train import sft
    from rl.train.policy_model import ModelConfig, PolicyModel

    trajectories = read_jsonl(args.trajectories)
    kept, funnel = sft.select(trajectories, sft.SFTConfig(
        max_per_question=args.max_per_question, min_reward=args.min_reward))
    print(sft.render_funnel(funnel))
    if not kept:
        print("\n没有可用的 SFT 数据。")
        return 1

    dataset = sft.build_dataset(kept)
    trainable = sum(sum(s.mask) for s in dataset)
    print(f"\n分词后 {len(dataset)} 条 · 可训练 {trainable} token "
          f"（占 {trainable / max(sum(len(s) for s in dataset), 1):.1%}）")

    model = PolicyModel(ModelConfig(
        model_name=args.base, learning_rate=args.lr, device=args.device,
        micro_batch_size=args.micro_batch_size,
    ))

    import random
    rng = random.Random(args.seed)
    step = 0
    for epoch in range(args.epochs):
        order = list(dataset)
        rng.shuffle(order)
        for start in range(0, len(order), args.batch_size):
            batch = order[start:start + args.batch_size]
            stats = model.sft_step(batch)
            step += 1
            if step % args.log_every == 0:
                print(f"  epoch {epoch} step {step:4}  loss {stats['loss']:.4f}  "
                      f"grad {stats['grad_norm']:.3f}  tokens {stats['trainable_tokens']}")

    path = model.save_adapter(args.out)
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
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--out", default=os.path.join(REPO_ROOT, "rl/data/sft/teacher.jsonl"))
    c.add_argument("--splits-dir", default=os.path.join(REPO_ROOT, "rl/data/splits"))
    c.set_defaults(func=collect)

    t = sub.add_parser("train", help="筛选并微调（需要 GPU）")
    t.add_argument("--trajectories", default=os.path.join(REPO_ROOT, "rl/data/sft/teacher.jsonl"))
    t.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
