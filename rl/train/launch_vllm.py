"""Launch a dedicated, optionally tensor-parallel rollout server.

    python -m rl.train.launch_vllm --base Qwen/Qwen3-8B --gpus 6,7 --tp 2
"""
import argparse
import os
import sys

from rl.model_storage import configure_hf_home


def command(base: str, gpus: str, tp: int, port: int, memory: float,
            max_model_len: int, host: str = "127.0.0.1") -> tuple[list[str], dict]:
    ids = [part.strip() for part in gpus.split(",") if part.strip()]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("--gpus 必须是不重复的 GPU ID 列表")
    if tp < 1 or tp != len(ids):
        raise ValueError("--tp 必须等于 --gpus 指定的卡数")
    if not 0 < memory <= 1:
        raise ValueError("--gpu-memory-utilization 必须在 (0, 1] 内")
    env = os.environ.copy()
    configure_hf_home(env)
    env["CUDA_VISIBLE_DEVICES"] = ",".join(ids)
    env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "1"
    # vLLM compiles CUDA kernels with ninja. The Python environment's bin/
    # must be on PATH even when this launcher is started outside activation.
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    argv = [
        sys.executable, "-m", "vllm.entrypoints.cli.main", "serve", base,
        "--served-model-name", base, "--tensor-parallel-size", str(tp),
        "--enable-lora", "--max-lora-rank", "32", "--max-loras", "1",
        "--host", host, "--port", str(port), "--gpu-memory-utilization", str(memory),
        "--max-model-len", str(max_model_len),
    ]
    return argv, env


def main() -> None:
    parser = argparse.ArgumentParser(description="启动多卡 vLLM rollout 服务")
    parser.add_argument("--base", required=True)
    parser.add_argument("--gpus", required=True, help="物理 GPU ID，例如 6,7")
    parser.add_argument("--tp", type=int, required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=16384)
    args = parser.parse_args()
    try:
        argv, env = command(args.base, args.gpus, args.tp, args.port,
                            args.gpu_memory_utilization, args.max_model_len, args.host)
    except ValueError as exc:
        parser.error(str(exc))
    os.execvpe(argv[0], argv, env)


if __name__ == "__main__":
    main()
