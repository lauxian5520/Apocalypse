"""Record progress for a detached GRPO training run."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def training_alive(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return b"torch.distributed.run" in command and b"rl.train.run_grpo" in command


def snapshot(args: argparse.Namespace) -> dict:
    pid = int(args.pid_file.read_text().strip())
    alive = training_alive(pid)
    rows = []
    if args.metrics.exists():
        with args.metrics.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    latest = rows[-1] if rows else None
    checkpoints = sorted(
        path for path in args.adapter_root.glob("policy-step-*")
        if (path / "adapter_model.safetensors").is_file()
    )
    completed = len(rows) >= args.total_steps and "指标写入" in (
        args.log.read_text(encoding="utf-8", errors="replace")[-4000:]
        if args.log.exists() else ""
    )
    age = round(time.time() - args.log.stat().st_mtime) if args.log.exists() else None
    return {
        "state": "completed" if completed else "running" if alive else "failed",
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "training_pid": pid,
        "training_alive": alive,
        "steps_recorded": len(rows),
        "total_steps": args.total_steps,
        "last_adapter": str(checkpoints[-1]) if checkpoints else None,
        "latest_metrics": latest,
        "log_age_seconds": age,
        "warning": "training log has not advanced for 60 minutes"
        if alive and age is not None and age > 3600 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--adapter-root", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--interval", type=int, default=60)
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
        summary = (status["state"], status["steps_recorded"], status["last_adapter"], status["warning"])
        if summary != previous:
            print(f"{status['checked_at']} {status['state']} "
                  f"steps={status['steps_recorded']}/{args.total_steps} "
                  f"adapter={status['last_adapter']} warning={status['warning']}", flush=True)
            previous = summary
        if status["state"] != "running":
            return 0 if status["state"] == "completed" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        sys.exit(f'Monitor failed: {type(exc).__name__}: {exc}')
