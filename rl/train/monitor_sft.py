"""Monitor a detached SFT run and leave its latest status on disk."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROGRESS = re.compile(r"epoch\s+(\d+)\s+step\s+(\d+)\s+loss\s+([\d.]+)")
ERROR = re.compile(r"Traceback|OutOfMemory|RuntimeError|NCCL.*(?:error|timeout)", re.I)


def training_alive(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return b"torch.distributed.run" in command and b"rl.train.run_sft" in command


def snapshot(args: argparse.Namespace) -> dict:
    pid = int(args.pid_file.read_text().strip())
    alive = training_alive(pid)
    log = args.log.read_text(encoding="utf-8", errors="replace") if args.log.exists() else ""
    progress = list(PROGRESS.finditer(log))
    latest = progress[-1] if progress else None
    errors = [line.strip() for line in log.splitlines() if ERROR.search(line)]
    checkpoints = sorted(
        path for path in args.checkpoints.glob("step-*")
        if (path / "adapter_model.safetensors").is_file()
        and (path / "adapter_config.json").is_file()
    )
    complete = (args.adapter / "adapter_model.safetensors").is_file() and "adapter 写入" in log
    state = "completed" if complete else "running" if alive else "failed"
    log_age = round(time.time() - args.log.stat().st_mtime) if args.log.exists() else None
    return {
        "state": state,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "training_pid": pid,
        "training_alive": alive,
        "step": int(latest.group(2)) if latest else 0,
        "total_steps": args.total_steps,
        "epoch_index": int(latest.group(1)) if latest else None,
        "loss": float(latest.group(3)) if latest else None,
        "last_checkpoint": str(checkpoints[-1]) if checkpoints else None,
        "checkpoint_count": len(checkpoints),
        "final_adapter": str(args.adapter) if complete else None,
        "log_age_seconds": log_age,
        "warning": "training log has not advanced for 30 minutes" if alive and log_age is not None and log_age > 1800 else None,
        "last_error": errors[-1] if errors else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.total_steps < 1 or args.interval < 1:
        parser.error('--total-steps and --interval must be positive')
    args.status.parent.mkdir(parents=True, exist_ok=True)

    previous = None
    while True:
        status = snapshot(args)
        temporary = args.status.with_suffix(args.status.suffix + ".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.status)
        summary = (status["state"], status["step"], status["last_checkpoint"], status["warning"])
        if summary != previous:
            print(f"{status['checked_at']} {status['state']} step={status['step']}/{args.total_steps} "
                  f"checkpoint={status['last_checkpoint']} warning={status['warning']}", flush=True)
            previous = summary
        if status["state"] != "running":
            return 0 if status["state"] == "completed" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        sys.exit(f'Monitor failed: {type(exc).__name__}: {exc}')
