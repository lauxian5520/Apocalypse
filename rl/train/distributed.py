"""Single-node FSDP2 setup and rank-zero orchestration helpers."""
import os


def world_size() -> int:
    return int(os.environ.get("WORLD_SIZE", "1"))


def validate_gpu_layout(rollout_gpus: str = "", tensor_parallel_size: int = 0) -> None:
    """Reject overlap between trainer and a locally launched vLLM server."""
    visible = [x.strip() for x in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if x.strip()]
    rollout = [x.strip() for x in rollout_gpus.split(",") if x.strip()]
    if len(rollout) != len(set(rollout)):
        raise SystemExit("--rollout-gpus 包含重复 GPU")
    if rollout and tensor_parallel_size and len(rollout) != tensor_parallel_size:
        raise SystemExit("--rollout-gpus 数量必须等于 --vllm-tp")
    if visible and len(visible) < world_size():
        raise SystemExit("torchrun 进程数超过训练进程可见的 GPU 数")
    if rollout and not visible:
        raise SystemExit("指定 --rollout-gpus 时，训练进程也须设置 CUDA_VISIBLE_DEVICES 以检查是否重叠")
    overlap = set(visible) & set(rollout)
    if overlap:
        raise SystemExit(f"训练和 vLLM GPU 重叠：{sorted(overlap)}")


def create_accelerator():
    """Initialize the process group before Transformers loads model weights."""
    if world_size() == 1:
        return None
    if "LOCAL_RANK" not in os.environ:
        raise SystemExit("多卡训练请用 torchrun 启动，每张训练卡一个进程")
    import torch
    if torch.cuda.device_count() < world_size():
        raise SystemExit("训练进程数超过可见 CUDA GPU 数；检查 CUDA_VISIBLE_DEVICES")
    from importlib.metadata import version
    from packaging.version import Version
    if Version(version("accelerate")) < Version("1.15.0"):
        raise SystemExit("FSDP2 + PEFT 多卡导出需要 accelerate>=1.15")
    from accelerate import Accelerator, FullyShardedDataParallelPlugin

    plugin = FullyShardedDataParallelPlugin(
        fsdp_version=2,
        auto_wrap_policy="transformer_based_wrap",
        reshard_after_forward=True,
        cpu_ram_efficient_loading=True,
        state_dict_type="SHARDED_STATE_DICT",
    )
    accelerator = Accelerator(fsdp_plugin=plugin, mixed_precision="no")
    if accelerator.num_processes != world_size():
        raise SystemExit("Accelerate 初始化的进程数与 WORLD_SIZE 不一致")
    return accelerator


def is_main(accelerator) -> bool:
    return accelerator is None or accelerator.is_main_process


def from_main(accelerator, function):
    """Run a CPU/HTTP operation once and send its result or error to every rank."""
    if accelerator is None:
        return function()
    import torch.distributed as dist

    payload = [None]
    if accelerator.is_main_process:
        try:
            payload[0] = (True, function())
        except Exception as exc:  # broadcast before raising, or workers deadlock
            payload[0] = (False, f"{type(exc).__name__}: {exc}")
    dist.broadcast_object_list(payload, src=0)
    ok, result = payload[0]
    if not ok:
        raise RuntimeError(f"主进程操作失败：{result}")
    return result
