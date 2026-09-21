"""Check the training environment before anything expensive happens.

Both training entry points import torch, transformers and peft somewhere down
their call chain. Without them the failure is a bare `ModuleNotFoundError`
raised from inside a module the user never mentioned, and — worse — it fires
*before* the vLLM reachability check, so someone whose real problem is a
misconfigured server gets told about torch instead.

So the order is fixed and cheap-first: dependencies, then the server, then the
corpus, then the model. Each stage's failure names the thing to do about it.
"""
import importlib
import shutil

# (module, pip name, what it is needed for)
TRAINING_DEPS = (
    ("torch", "torch", "forward/backward"),
    ("transformers", "transformers", "模型与分词器"),
    ("peft", "peft", "LoRA"),
)

ROLLOUT_DEPS = (
    ("transformers", "transformers", "chat template 与分词器"),
    ("httpx", "httpx", "调用 provider / vLLM"),
)

CORPUS_DEPS = (
    ("pyarrow", "pyarrow", "读 HotpotQA 的 parquet"),
)


def _missing(deps):
    out = []
    for module, package, why in deps:
        try:
            importlib.import_module(module)
        except ImportError:
            out.append((module, package, why))
    return out


def require(deps, phase: str) -> None:
    missing = _missing(deps)
    if not missing:
        return
    lines = [f"{phase} 缺少依赖："]
    for module, _, why in missing:
        lines.append(f"  - {module}（{why}）")
    lines.append("")
    lines.append("安装：")
    lines.append("  pip install -r rl/requirements.txt -r rl/requirements-trainer.txt")
    lines.append("")
    lines.append("注意 Python 版本：训练侧目标是 3.11 / 3.12。torch 在本仓库开发机的")
    lines.append("默认 3.14 解释器上装不上，所以 rl/ 的分词与 rollout 部分能在默认环境")
    lines.append("跑，训练必须在单独的 3.11/3.12 环境（或 GPU 机）里跑。")
    raise SystemExit("\n".join(lines))


def require_training() -> None:
    require(TRAINING_DEPS, "训练")


def require_rollout() -> None:
    require(ROLLOUT_DEPS, "Rollout")


def require_corpus() -> None:
    require(CORPUS_DEPS, "语料构建")


def describe_device(device: str) -> str:
    """One line about what we are about to train on, or why we cannot.

    Reported up front because "it is running" and "it is running on the CPU at
    one step per minute" look identical for the first few minutes, and only one
    of them is worth paying for.
    """
    try:
        import torch
    except ImportError:
        return "torch 不可用"

    if device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise SystemExit(
                "指定了 --device cuda，但 torch 看不到 CUDA 设备。\n"
                "  - 在 GPU 机上：检查驱动与 torch 的 CUDA 版本是否匹配\n"
                "  - 只想验证链路：加 --device cpu（会非常慢，只适合 --smoke）"
            )
        # Report the card the trainer will actually use. `current_device()` is
        # always 0 unless someone called set_device, so `--device cuda:1` would
        # otherwise print — and warn about — the card vLLM is sitting on.
        index = torch.device(device).index
        if index is None:
            index = torch.cuda.current_device()
        name = torch.cuda.get_device_name(index)
        free, total = torch.cuda.mem_get_info(index)
        line = (f"{name} · {total / 1e9:.0f} GB · 空闲 {free / 1e9:.1f} GB · "
                f"torch {torch.__version__}")
        if free < LOW_FREE_BYTES:
            line += "\n" + _vllm_share_hint(free)
        return line
    return f"CPU · torch {torch.__version__}（仅适合 --smoke，正式训练会慢到不可用）"


# Below this the trainer is unlikely to fit even the 0.5B smoke model once
# activations and the 150k-vocab logits are counted.
LOW_FREE_BYTES = 6 * 1024**3


def _vllm_share_hint(free: int) -> str:
    """Why a GPU that is 'available' has no room, and the flag that fixes it.

    vLLM pre-allocates `--gpu-memory-utilization` of the *whole* card for its
    KV cache, and the default is 0.9. Started first on the same GPU — which is
    what the quickstart does — it leaves the trainer about a tenth of the card:
    2.4 GB of a 24 GB 4090, less than the 1.5B model's bf16 weights alone. The
    result is an OOM with nothing in it pointing at vLLM.
    """
    return (
        f"  ⚠ 只剩 {free / 1e9:.1f} GB 空闲。若 vLLM 与训练共用这张卡，vLLM 默认会预占 90% 显存。\n"
        "    重启 vLLM 时加 --gpu-memory-utilization 0.35，或用 CUDA_VISIBLE_DEVICES 把它放到另一张卡上。"
    )


def oom_message() -> str:
    """What to say when training runs out of memory anyway."""
    return (
        "显存不足（CUDA out of memory）。\n"
        "  1. 若 vLLM 在同一张卡上：启动时加 --gpu-memory-utilization 0.35（默认 0.9 会占掉九成显存）\n"
        "  2. 仍不够：依次调小 --max-tokens、--batch-size、-G，或先换 0.5B 基座跑通\n"
        "     详见 rl/GPU_QUICKSTART.md「显存不够时」"
    )


def warn_if_no_nvidia_smi() -> str:
    return "" if shutil.which("nvidia-smi") else "找不到 nvidia-smi，这台机器大概没有 GPU"
