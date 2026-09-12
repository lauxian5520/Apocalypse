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
        index = torch.cuda.current_device()
        name = torch.cuda.get_device_name(index)
        total = torch.cuda.get_device_properties(index).total_memory / 1e9
        return f"{name} · {total:.0f} GB · torch {torch.__version__}"
    return f"CPU · torch {torch.__version__}（仅适合 --smoke，正式训练会慢到不可用）"


def warn_if_no_nvidia_smi() -> str:
    return "" if shutil.which("nvidia-smi") else "找不到 nvidia-smi，这台机器大概没有 GPU"
