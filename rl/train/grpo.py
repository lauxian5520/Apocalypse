"""GRPO: group-relative advantages and a token-level clipped policy loss.

Small on purpose. The surrounding machinery — rollout, reward, packing — is
already built and tested, so what is left is the objective itself, and the
objective is where the decisions that matter live. Three of them:

**No value head.** The baseline is the mean reward of the *group*: G rollouts of
the same question. A learned critic on a 1.5B policy would be a second model to
train, tune and distrust, and the group mean is an unbiased baseline that costs
nothing. That is the whole idea of GRPO.

**Token-level normalisation, not per-sequence.** The loss sums over every
unmasked token in the batch and divides by their total count — *not* the mean of
per-sequence means. In multi-turn agentic RL the difference is large and
systematic: trajectories here run 1,800–3,300 tokens with only 7–12% trainable
(measured), and a per-sequence mean weights a 3-step trajectory the same as a
12-step one, so it silently down-weights exactly the long-horizon behaviour the
environment exists to train. This is the DAPO choice and it is worth being able
to say why.

**`logp_old` comes from the rollout, not from a second forward pass.** The
sampling policy's log-probabilities are recorded when the tokens are generated
(`policy_adapter.py` requests them alongside the completion). Recomputing them
later would give the *current* policy's numbers, making the ratio identically 1
and the clipping dead — the bug looks like "clipping never triggers", which is
easy to read as good news.

The code is deliberately framework-free: it takes tensors and returns a scalar,
so it runs under plain `transformers` + `peft` on one rented GPU, and the same
function is what a verl integration would call.
"""
from dataclasses import dataclass

import torch
import torch.nn.functional as F


@dataclass
class GRPOConfig:
    clip_eps: float = 0.2
    kl_coef: float = 0.02
    # Groups whose rewards are all equal carry no signal: the advantage is
    # exactly zero for every member, so they contribute nothing but compute.
    # Dropping them is free and keeps the reported gradient-norm honest.
    drop_degenerate_groups: bool = True
    # Below this reward spread a group counts as degenerate.
    min_group_std: float = 1e-6
    # Guards against a single group with near-identical rewards blowing up the
    # normalised advantage.
    advantage_eps: float = 1e-4


def group_advantages(
    rewards: torch.Tensor,
    group_ids: torch.Tensor,
    config: GRPOConfig | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Centre and scale each group's rewards. Returns (advantages, keep_mask).

    `rewards[i]` is the scalar reward of trajectory `i`; `group_ids[i]` says
    which question it came from. Every trajectory in a group answered the *same*
    question, so the group mean is the natural baseline: "was this rollout
    better than my other attempts at this question?"
    """
    cfg = config or GRPOConfig()
    advantages = torch.zeros_like(rewards)
    keep = torch.ones_like(rewards, dtype=torch.bool)

    for gid in torch.unique(group_ids):
        members = group_ids == gid
        group = rewards[members]
        std = group.std(unbiased=False)

        if cfg.drop_degenerate_groups and std < cfg.min_group_std:
            # All-zero (nobody solved it) or all-one (everybody did). Both are
            # real and both are useless; the online curriculum retires such
            # questions from the training pool.
            keep[members] = False
            continue

        advantages[members] = (group - group.mean()) / (std + cfg.advantage_eps)

    return advantages, keep


def masked_token_loss(
    logits: torch.Tensor,
    input_ids: torch.Tensor,
    mask: torch.Tensor,
    advantages: torch.Tensor,
    logp_old: torch.Tensor,
    ref_logp: torch.Tensor | None = None,
    config: GRPOConfig | None = None,
) -> tuple[torch.Tensor, dict]:
    """The clipped policy-gradient loss over masked tokens.

    Shapes — everything is passed in *sequence* alignment and shifted here:

    - `logits`     (B, T, V)  from the policy; position `t` predicts token `t+1`
    - `input_ids`  (B, T)
    - `mask`       (B, T)     1 where the policy generated that token
    - `advantages` (B,)       one scalar per trajectory
    - `logp_old`   (B, T)     see below
    - `ref_logp`   (B, T)     frozen reference, same convention, for the KL

    **`logp_old[b, t]` is the log-probability of `input_ids[b, t]` itself** —
    token-aligned, not logit-aligned. Position 0 is unused (nothing predicts the
    first token) and may be anything. Get this backwards by one and the ratio is
    computed against the wrong token: the loss still runs, the gradient is still
    finite, and the model trains on noise. `rl/checks/rl_check.py` pins the
    convention with a fixture where the policy and the sampler are identical, so
    the ratio must come out at exactly 1.
    """
    cfg = config or GRPOConfig()

    # Shift: logits at position t predict input_ids at t+1.
    logits = logits[:, :-1, :]
    targets = input_ids[:, 1:]
    mask = mask[:, 1:].to(logits.dtype)
    logp_old = logp_old[:, 1:]

    logp = torch.gather(
        F.log_softmax(logits, dim=-1), dim=-1, index=targets.unsqueeze(-1)
    ).squeeze(-1)

    ratio = torch.exp(logp - logp_old)
    adv = advantages.unsqueeze(1)                    # broadcast over time

    unclipped = ratio * adv
    clipped = torch.clamp(ratio, 1.0 - cfg.clip_eps, 1.0 + cfg.clip_eps) * adv
    policy_loss = -torch.min(unclipped, clipped)

    total_tokens = mask.sum().clamp(min=1.0)
    loss = (policy_loss * mask).sum() / total_tokens

    stats = {
        "policy_loss": loss.detach(),
        "trainable_tokens": total_tokens.detach(),
        # The fraction of tokens where clipping actually bound. Near zero means
        # the update is tiny; near one means the policy has run away from the
        # sampling distribution and the step should be smaller.
        "clip_fraction": (
            ((ratio < 1 - cfg.clip_eps) | (ratio > 1 + cfg.clip_eps)).to(logits.dtype) * mask
        ).sum().detach() / total_tokens,
        "mean_ratio": ((ratio * mask).sum() / total_tokens).detach(),
    }

    if ref_logp is not None:
        ref_logp = ref_logp[:, 1:]
        # k3 estimator: always non-negative and lower variance than (logp_old -
        # logp), which can go negative on a sample and makes the reported KL
        # hard to read.
        diff = ref_logp - logp
        kl = torch.exp(diff) - diff - 1.0
        kl_term = (kl * mask).sum() / total_tokens
        loss = loss + cfg.kl_coef * kl_term
        stats["kl"] = kl_term.detach()

    stats["loss"] = loss.detach()
    return loss, stats


def masked_cross_entropy(
    logits: torch.Tensor, input_ids: torch.Tensor, mask: torch.Tensor
) -> torch.Tensor:
    """Token-level cross-entropy over masked positions — the SFT objective.

    Lives beside the RL loss because it must use the *same* shift and the *same*
    normalisation. If SFT and GRPO disagreed about which positions the policy
    produced, the cold start would teach one thing and the RL phase would
    reinforce another, and nothing would report a conflict.

    That shared construction is also what makes SFT the canary for the mask: a
    span off by a token, or a stop token left outside it, makes SFT fail to
    converge on a run costing a fraction of an RL run.
    """
    logits = logits[:, :-1, :]
    targets = input_ids[:, 1:]
    mask = mask[:, 1:].to(logits.dtype)

    logp = F.log_softmax(logits, dim=-1)
    token_loss = -logp.gather(dim=-1, index=targets.unsqueeze(-1)).squeeze(-1)
    return (token_loss * mask).sum() / mask.sum().clamp(min=1.0)


def token_logprobs(logits: torch.Tensor, input_ids: torch.Tensor) -> torch.Tensor:
    """Token-aligned log-probabilities: `out[b, t] = log P(input_ids[b, t])`.

    The one place the shift is written down. `logits[b, t]` predicts token
    `t + 1`, so the log-prob *of* token `t` comes from `logits[b, t - 1]`;
    position 0 is left at zero because nothing predicts the first token.

    This is the convention `masked_token_loss` expects for `logp_old` and
    `ref_logp`, and it is what a frozen reference model's forward pass should be
    run through. It exists because the shift was gotten backwards three separate
    times while writing tests for this module — each time producing a plausible
    finite loss and a wrong gradient. A named function is cheaper than
    rediscovering the off-by-one.
    """
    shifted = F.log_softmax(logits[:, :-1, :], dim=-1)
    picked = torch.gather(shifted, dim=-1, index=input_ids[:, 1:].unsqueeze(-1)).squeeze(-1)
    return torch.cat([torch.zeros_like(picked[:, :1]), picked], dim=1)


def entropy(logits: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Mean token entropy over masked positions.

    Tracked rather than optimised. A collapse here is the earliest visible sign
    that the policy has stopped exploring, and it shows up well before the
    reward curve flattens.
    """
    logits = logits[:, :-1, :]
    mask = mask[:, 1:].to(logits.dtype)
    logp = F.log_softmax(logits, dim=-1)
    token_entropy = -(logp.exp() * logp).sum(dim=-1)
    return (token_entropy * mask).sum() / mask.sum().clamp(min=1.0)
