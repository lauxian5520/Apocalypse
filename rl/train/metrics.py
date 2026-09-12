"""The curves a GRPO run has to show, and why each one is on the list.

A reward curve alone cannot tell you whether a run is healthy. These can, and
each is here because it catches a specific failure that the reward curve hides:

| metric | what its failure looks like |
|---|---|
| `all_zero_groups` | no gradient at all; reward flat and *nothing is wrong* in the loss |
| `clip_fraction` | ~0 means the step did nothing; ~1 means the policy ran away from the sampler |
| `kl` | climbing means drifting off the reference, usually just before collapse |
| `entropy` | falling fast means exploration is dead and the reward will plateau |
| `format_valid` | falling means the policy is losing the tool protocol, not getting smarter |
| `gave_up` | rising means it learned that answering nothing is cheaper than searching |
| `mean_tool_calls` | falling to ~1 means it stopped researching and started guessing |
| `voided` | rising means it found a way to farm the verifier |

`voided` and `gave_up` are the two that matter most for this environment,
because they are how reward hacking and reward collapse actually show up here —
and both can rise while mean reward also rises.

Written as JSONL, one row per step, so the run is plottable afterwards without
a plotting dependency on the machine that produced it.
"""
import json
import os
from dataclasses import dataclass, field, asdict


@dataclass
class StepMetrics:
    """One optimiser step."""

    step: int = 0
    # ── reward side ───────────────────────────────────────────────
    mean_reward: float = 0.0
    pass_at_1: float = 0.0
    format_valid: float = 0.0
    voided: float = 0.0
    gave_up: float = 0.0
    mean_steps: float = 0.0
    mean_tool_calls: float = 0.0
    mean_grounding_f1: float = 0.0
    # ── optimisation side ─────────────────────────────────────────
    loss: float = 0.0
    kl: float = 0.0
    entropy: float = 0.0
    clip_fraction: float = 0.0
    mean_ratio: float = 0.0
    grad_norm: float = 0.0
    trainable_tokens: int = 0
    # ── signal health ─────────────────────────────────────────────
    groups: int = 0
    all_zero_groups: float = 0.0
    curriculum_active: int = 0
    # ── cost ──────────────────────────────────────────────────────
    rollout_seconds: float = 0.0
    train_seconds: float = 0.0
    adapter: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetricLog:
    path: str = ""
    rows: list[StepMetrics] = field(default_factory=list)

    def append(self, metrics: StepMetrics) -> None:
        self.rows.append(metrics)
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        # Appended and flushed per step: a run that dies at step 400 must keep
        # 400 steps of curve, not lose them to a buffer.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics.to_dict(), ensure_ascii=False))
            f.write("\n")
            f.flush()

    def warnings(self, metrics: StepMetrics) -> list[str]:
        """Health checks worth interrupting a run for.

        Deliberately phrased as what to *do*, not just what is wrong: these fire
        in the middle of a long run, and a warning that only says "entropy is
        low" makes the reader re-derive the implication every time.
        """
        out = []
        if metrics.all_zero_groups > 0.7:
            out.append(
                f"{metrics.all_zero_groups:.0%} 的组奖励全零——这些组的 advantage 恒为 0，"
                f"梯度为零而算力照付。策略太弱，应该先做 SFT 冷启动"
            )
        if metrics.groups and metrics.clip_fraction < 1e-4:
            out.append("clip_fraction≈0：这一步几乎没有更新，检查 logp_old 是否被误重算")
        if metrics.clip_fraction > 0.5:
            out.append(f"clip_fraction={metrics.clip_fraction:.0%}：策略已跑离采样分布，应减小步长")
        if metrics.gave_up > 0.3:
            out.append(
                f"{metrics.gave_up:.0%} 的轨迹放弃作答——塑形项可能在压倒稀疏的 outcome，"
                f"确认塑形是否门控在正确性之后"
            )
        if metrics.voided > 0.1:
            out.append(f"{metrics.voided:.0%} 被反作弊作废：策略在学绕过验证器，去看 reward hacking 审计")
        if metrics.format_valid < 0.5 and metrics.step > 20:
            out.append(f"格式合法率只有 {metrics.format_valid:.0%}：策略正在丢掉工具协议")
        return out

    def render(self, last: int = 10) -> str:
        if not self.rows:
            return "（还没有记录）"
        head = (f"{'step':>5} {'reward':>7} {'pass@1':>7} {'fmt':>6} {'void':>6} "
                f"{'giveup':>7} {'kl':>7} {'clip':>6} {'ent':>6} {'0组':>6}")
        lines = [head, "-" * len(head)]
        for r in self.rows[-last:]:
            lines.append(
                f"{r.step:>5} {r.mean_reward:>7.3f} {r.pass_at_1:>7.1%} "
                f"{r.format_valid:>6.0%} {r.voided:>6.0%} {r.gave_up:>7.0%} "
                f"{r.kl:>7.4f} {r.clip_fraction:>6.1%} {r.entropy:>6.3f} "
                f"{r.all_zero_groups:>6.0%}"
            )
        return "\n".join(lines)


def from_rewards(rewards: list[dict]) -> dict:
    """Roll a step's trajectory rewards into the reward-side metrics."""
    n = max(len(rewards), 1)

    def frac(pred):
        return sum(1 for r in rewards if pred(r)) / n

    def mean(key):
        return sum(float(r.get(key) or 0.0) for r in rewards) / n

    return {
        "mean_reward": mean("total"),
        "pass_at_1": frac(lambda r: r.get("correct")),
        "format_valid": frac(lambda r: r.get("format_valid")),
        "voided": frac(lambda r: r.get("voided")),
        "gave_up": frac(lambda r: r.get("ended_by") in ("no-tool-call", "max-steps")),
        "mean_steps": mean("steps"),
        "mean_tool_calls": mean("tool_calls"),
        "mean_grounding_f1": mean("grounding_f1"),
    }
