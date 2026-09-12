"""Exporting trajectories so a real RL framework can train on them.

The trainer in this repository is deliberately small: one 4090, LoRA, a few
hundred steps. That is enough to produce a learning curve and an ablation table,
which is what the project is for. It is not enough to claim the environment
scales, and "it would work on a cluster" is not a claim worth making without
something a cluster can actually read.

So trajectories export in two formats, both of which drop the assumption that
*this* trainer is the one consuming them:

- **`messages`** — the OpenAI conversation, exactly as `derive_messages()`
  produced it, plus the reward. This is what verl's and TRL's multi-turn SFT
  paths want, and it is readable by anything.
- **`tokens`** — ids and the loss mask from `template.pack()`. This is the
  format that preserves *which tokens the policy actually generated*, which the
  message form cannot express: a consumer re-tokenising `messages` would have to
  rediscover the mask, and rediscovering it is exactly where the `tojson`
  double-encoding bug lives. Any consumer that wants to train on assistant
  tokens only should read this one.

Both carry the environment stamp — corpus hash, preset, step cap, chat-template
hash — because a trajectory without it cannot be compared to another.
"""
import json
import os
from dataclasses import dataclass

from harness.session.projection import derive_messages
from harness.tools.registry import ToolRegistry

from rl.env import template
from rl.env.build import PRESET
from rl.rollout.trajectory import Trajectory


@dataclass
class ExportStats:
    written: int = 0
    skipped_errored: int = 0
    skipped_untokenisable: int = 0

    def render(self) -> str:
        return (f"导出 {self.written} 条"
                f"（跳过：出错 {self.skipped_errored} · 无法分词 {self.skipped_untokenisable}）")


def _base_row(trajectory: Trajectory) -> dict:
    reward = trajectory.reward or {}
    return {
        "task_id": trajectory.task_id,
        "question": trajectory.question,
        "gold_answer": trajectory.gold_answer,
        "reward": float(reward.get("total") or 0.0),
        "correct": bool(reward.get("correct")),
        # Carried so a consumer can filter or re-weight without re-running the
        # verifier, and so a reward recomputed elsewhere can be diffed.
        "reward_breakdown": reward,
        "env": trajectory.stamp,
    }


def export(
    trajectories: list[Trajectory],
    out_path: str,
    fmt: str = "messages",
    tools: list[dict] | None = None,
) -> ExportStats:
    """Write `trajectories` as JSONL in `fmt` ("messages" or "tokens")."""
    if fmt not in ("messages", "tokens"):
        raise ValueError(f"未知格式 {fmt!r}，可选 messages / tokens")
    if tools is None:
        tools = ToolRegistry(PRESET).schemas()

    stats = ExportStats()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        for trajectory in trajectories:
            if not trajectory.ok:
                stats.skipped_errored += 1
                continue

            row = _base_row(trajectory)
            log = trajectory.session_events()

            if fmt == "messages":
                row["messages"] = derive_messages(log, "")
                row["tools"] = tools
            else:
                try:
                    packed = template.pack(log, tools)
                except AssertionError:
                    # The prefix property failed, so the mask cannot be trusted.
                    # Exporting it anyway would hand a downstream trainer a
                    # silently wrong target.
                    stats.skipped_untokenisable += 1
                    continue
                if not packed.segments:
                    stats.skipped_untokenisable += 1
                    continue
                row["input_ids"] = packed.token_ids
                row["loss_mask"] = packed.mask
                row["assistant_spans"] = [[s.start, s.end] for s in packed.segments]
                row["template_sha256"] = packed.template_sha256

            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            stats.written += 1

    return stats


def verify(path: str, fmt: str) -> dict:
    """Read an export back and check it is self-consistent.

    Worth doing separately from writing it: an export is handed to another
    system, and "it looked fine when we wrote it" is not a property the other
    system can rely on.
    """
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise AssertionError(f"{path} 是空的")

    stamps = {json.dumps(r.get("env"), sort_keys=True) for r in rows}
    if len(stamps) > 1:
        raise AssertionError(
            f"{len(stamps)} 种不同的环境指纹混在一个导出文件里——"
            "这些轨迹不是在同一个环境下采集的，不能混训"
        )

    if fmt == "tokens":
        for row in rows:
            if len(row["input_ids"]) != len(row["loss_mask"]):
                raise AssertionError(f"{row['task_id']}: ids 与 mask 长度不一致")
            if not any(row["loss_mask"]):
                raise AssertionError(f"{row['task_id']}: mask 全零，没有可训练 token")
            for start, end in row["assistant_spans"]:
                if not all(row["loss_mask"][i] for i in range(start, end)):
                    raise AssertionError(f"{row['task_id']}: span [{start},{end}) 内有未标记 token")
        trainable = sum(sum(r["loss_mask"]) for r in rows)
        total = sum(len(r["input_ids"]) for r in rows)
    else:
        for row in rows:
            roles = [m["role"] for m in row["messages"]]
            if not roles or roles[0] != "system":
                raise AssertionError(f"{row['task_id']}: 消息不是以 system 开头")
            if "assistant" not in roles:
                raise AssertionError(f"{row['task_id']}: 没有 assistant 消息")
        trainable = total = 0

    out = {"rows": len(rows), "format": fmt}
    if total:
        out["trainable_fraction"] = round(trainable / total, 4)
    return out
