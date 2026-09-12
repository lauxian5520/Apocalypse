"""GRPO entry point: the runnable driver.

    # smoke: tiny model, 2 questions, 1 step — proves the whole loop before
    # a real run spends GPU hours on a typo
    python -m rl.train.run_grpo --smoke --base Qwen/Qwen2.5-0.5B-Instruct \\
        --vllm http://127.0.0.1:8000

    # real
    python -m rl.train.run_grpo --base Qwen/Qwen2.5-1.5B-Instruct \\
        --adapter rl/data/adapters/sft --vllm http://127.0.0.1:8000 \\
        --steps 500 -G 8 --questions-per-step 8

vLLM must be started separately, with runtime LoRA updating on:

    VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-1.5B-Instruct \\
        --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000

Two processes rather than one, because the trainer holds optimiser state and the
server holds KV cache, and on a single 24 GB card they do not both fit in one
allocator. The consequence is that weights reach the sampler through a LoRA swap
(`lora_serve.py`), which is also why the flag above is mandatory: without it the
swap silently no-ops and every rollout for the rest of the run comes from the
base model while the loss curve happily moves.
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


class Hooks:
    """`loop.TrainHooks` over a real model and a real vLLM server."""

    def __init__(self, model, server, adapter_root, eval_fn, grpo_config=None):
        self.model = model
        self.server = server
        self.adapter_root = adapter_root
        self.eval_fn = eval_fn
        self.grpo_config = grpo_config

    def forward_backward(self, batch, advantages, keep) -> dict:
        return self.model.grpo_step(batch, advantages, keep, self.grpo_config)

    def publish(self, step: int) -> str:
        from rl.train.lora_serve import adapter_dir, adapter_name

        path = adapter_dir(self.adapter_root, step)
        self.model.save_adapter(path)
        name = adapter_name(step)
        self.server.load(name, path)
        return name

    def evaluate(self, step: int) -> dict:
        return self.eval_fn(step)


def build_rollout(tasks_by_id, policy_factory, stamp, concurrency: int):
    """A `rollout(chosen, G)` closure over the harness loop."""
    from rl.rollout import engine

    def rollout(chosen, group_size):
        # A factory, not an instance: the engine builds one adapter per episode
        # so each trajectory owns its own generation records, which is what
        # makes `logp_old` attributable. See `engine.run_episode`.
        trajectories, _ = asyncio.run(engine.run_many(
            chosen, policy_factory, stamp, concurrency=concurrency,
            group_size=group_size, schedule="refill", progress_every=0,
        ))
        return trajectories

    return rollout


def make_evaluator(args, stamp, policy_factory):
    """Score the dev split with whatever the server is currently serving."""
    from rl.rollout import engine
    from rl.tasks import split as split_mod
    from rl.verifiers import reward as reward_verifier

    dev = split_mod.load(args.splits_dir, "dev")
    if args.eval_n:
        import random
        dev = random.Random(0).sample(dev, min(args.eval_n, len(dev)))

    def evaluate(step: int) -> dict:
        llm = policy_factory(temperature=0.0)
        trajectories, _ = asyncio.run(engine.run_many(
            dev, engine.shared(llm), stamp, concurrency=args.concurrency,
            group_size=1, schedule="refill", progress_every=0,
        ))
        ok = [t for t in trajectories if t.ok]
        n = max(len(ok), 1)
        return {
            "n": len(ok),
            "pass@1": round(sum(1 for t in ok if t.reward.get("correct")) / n, 4),
            "format_valid": round(sum(1 for t in ok if t.reward.get("format_valid")) / n, 4),
            "gave_up": round(sum(
                1 for t in ok if t.reward.get("ended_by") in ("no-tool-call", "max-steps")
            ) / n, 4),
            "mean_tool_calls": round(sum(t.reward.get("tool_calls") or 0 for t in ok) / n, 2),
        }

    return evaluate


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    p = argparse.ArgumentParser(description="GRPO 训练")
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--adapter", default="", help="起始 LoRA（通常是 SFT 的产物）")
    p.add_argument("--vllm", default="http://127.0.0.1:8000")
    p.add_argument("--steps", type=int, default=500)
    p.add_argument("--questions-per-step", type=int, default=8)
    p.add_argument("-G", "--group-size", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--micro-batch-size", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--lr", type=float, default=1e-6)
    p.add_argument("--kl-coef", type=float, default=0.02)
    p.add_argument("--clip-eps", type=float, default=0.2)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--device", default="cuda")
    p.add_argument("--eval-every", type=int, default=25)
    p.add_argument("--eval-n", type=int, default=100)
    p.add_argument("--save-every", type=int, default=25)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--split", default="train")
    p.add_argument("--splits-dir", default=os.path.join(REPO_ROOT, "rl/data/splits"))
    p.add_argument("--adapter-root", default=os.path.join(REPO_ROOT, "rl/data/adapters/grpo"))
    p.add_argument("--metrics", default=os.path.join(REPO_ROOT, "rl/data/metrics/grpo.jsonl"))
    p.add_argument("--smoke", action="store_true",
                   help="2 题 · G=2 · 1 步 · 每步都换权重与评测，用来验证全链路")
    args = p.parse_args()

    if args.smoke:
        args.steps, args.questions_per_step, args.group_size = 1, 2, 2
        args.eval_every = args.save_every = 1
        args.eval_n = 4
        args.concurrency = min(args.concurrency, 4)

    # Cheapest first: a missing dependency must not surface as a traceback from
    # inside a module the user never mentioned, and must not pre-empt the vLLM
    # check for someone whose real problem is the server.
    from rl.train import preflight

    preflight.require_training()

    from harness.corpus.store import load_cached
    from harness.tools.registry import ToolRegistry
    from rl.env import template
    from rl.env.build import PRESET, corpus_dir
    from rl.env.policy_adapter import PolicyAdapter
    from rl.rollout.trajectory import EnvStamp
    from rl.tasks import split as split_mod
    from rl.train import grpo
    from rl.train.loop import LoopConfig, run
    from rl.train.lora_serve import VLLMServer
    from rl.train.policy_model import ModelConfig, PolicyModel

    print(f"设备：{preflight.describe_device(args.device)}")
    hint = preflight.warn_if_no_nvidia_smi()
    if hint:
        print(f"  ⚠ {hint}")

    # Fail before loading a model if the server cannot accept weight swaps.
    server = VLLMServer(args.vllm)
    served = server.wait_ready()
    server.check_runtime_lora()
    print(f"vLLM 就绪：{served}")

    store = load_cached(corpus_dir(), False)
    registry = ToolRegistry(PRESET)
    stamp = EnvStamp(
        corpus_sha256=store.manifest.docs_sha256, corpus_docs=len(store),
        preset=PRESET, max_steps=registry.max_steps, model=args.base,
        template_sha256=template.template_sha256(),
    )

    tasks = split_mod.load(args.splits_dir, args.split)
    if args.smoke:
        tasks = tasks[: args.questions_per_step]
    print(f"训练集 {len(tasks)} 题 · 语料 {len(store)} 段 @ {stamp.corpus_sha256[:12]}…")

    served_name = {"name": args.base}

    def policy_factory(temperature: float = 1.0):
        return PolicyAdapter(
            args.vllm, served_name["name"],
            max_tokens=args.max_tokens, temperature=temperature, top_p=0.95,
        )

    model = PolicyModel(ModelConfig(
        model_name=args.base, learning_rate=args.lr, device=args.device,
        micro_batch_size=args.micro_batch_size,
    ))
    if args.adapter:
        model.model.load_adapter(args.adapter, adapter_name="default", is_trainable=True)
        print(f"从 {args.adapter} 继续")

    evaluate = make_evaluator(args, stamp, policy_factory)

    class TrackingHooks(Hooks):
        def publish(self, step: int) -> str:
            name = super().publish(step)
            # Subsequent rollouts must request the adapter, not the base model.
            served_name["name"] = name
            return name

    hooks = TrackingHooks(
        model, server, args.adapter_root, evaluate,
        grpo.GRPOConfig(clip_eps=args.clip_eps, kl_coef=args.kl_coef),
    )

    state = run(
        tasks, hooks,
        build_rollout({t["task_id"]: t for t in tasks}, policy_factory, stamp, args.concurrency),
        LoopConfig(
            steps=args.steps, questions_per_step=args.questions_per_step,
            group_size=args.group_size, batch_size=args.batch_size,
            concurrency=args.concurrency, eval_every=args.eval_every,
            save_every=args.save_every, seed=args.seed,
        ),
        metrics_path=args.metrics,
    )

    print()
    print(state.log.render(last=20))
    print()
    print(f"课程池：{state.pool.summary()}")
    if state.eval_history:
        print(f"dev 轨迹：{state.eval_history}")
    print(f"指标写入 {args.metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
