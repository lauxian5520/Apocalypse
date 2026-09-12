"""The trainable policy: a base model plus a LoRA adapter, and one optimiser step.

This is the GPU half of training — the three `TrainHooks` methods `loop.py`
declares. Everything it touches (torch, transformers, peft) is imported lazily
so importing this module on a machine without them still works; only calling it
requires a card.

Two decisions here are the ones worth understanding.

**The reference model for the KL term is this same model with the adapter
switched off.** `peft` exposes `disable_adapter()` as a context manager, and
LoRA leaves the base weights untouched, so the reference policy is already in
memory. Loading a second frozen copy would double the weights in VRAM for
nothing — on a 24 GB card that is the difference between fitting and not.

**Gradient accumulation is over tokens, not batches.** The loss divides by the
number of unmasked tokens in the batch, and batches here are ragged (1,800 to
3,300 tokens, 7-12% trainable). Averaging per-batch losses would weight a batch
of short trajectories the same as a batch of long ones, reintroducing exactly
the bias `grpo.masked_token_loss` avoids inside a batch. So accumulation sums
weighted losses and divides once, at the end.
"""
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_LORA_R = 32
DEFAULT_LORA_ALPHA = 64
DEFAULT_LORA_DROPOUT = 0.0
# Attention + MLP projections. Attention-only LoRA is cheaper but consistently
# weaker at learning a new output *format*, which is much of what this task asks
# for (emitting well-formed tool calls).
DEFAULT_TARGET_MODULES = (
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
)


@dataclass
class ModelConfig:
    model_name: str = "Qwen/Qwen2.5-1.5B-Instruct"
    lora_r: int = DEFAULT_LORA_R
    lora_alpha: int = DEFAULT_LORA_ALPHA
    lora_dropout: float = DEFAULT_LORA_DROPOUT
    target_modules: tuple = DEFAULT_TARGET_MODULES
    learning_rate: float = 1e-5
    weight_decay: float = 0.0
    max_grad_norm: float = 1.0
    dtype: str = "bfloat16"
    device: str = "cuda"
    gradient_checkpointing: bool = True
    # Split each batch into micro-batches of this many sequences before the
    # forward pass. Sequences here are long; two 3k-token sequences with a
    # 150k-vocab logit tensor is already several GB.
    micro_batch_size: int = 1


@dataclass
class StepStats:
    loss: float = 0.0
    kl: float = 0.0
    entropy: float = 0.0
    clip_fraction: float = 0.0
    mean_ratio: float = 0.0
    grad_norm: float = 0.0
    trainable_tokens: int = 0
    skipped: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {
            "loss": round(self.loss, 6),
            "kl": round(self.kl, 6),
            "entropy": round(self.entropy, 4),
            "clip_fraction": round(self.clip_fraction, 4),
            "mean_ratio": round(self.mean_ratio, 4),
            "grad_norm": round(self.grad_norm, 4),
            "trainable_tokens": self.trainable_tokens,
        }
        out.update(self.extra)
        return out


class PolicyModel:
    """A LoRA-wrapped causal LM with GRPO and SFT steps."""

    def __init__(self, config: ModelConfig | None = None, tokenizer=None):
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM

        self.config = cfg = config or ModelConfig()
        self.torch = torch

        dtype = getattr(torch, cfg.dtype)
        logger.info("[policy] loading %s (%s)", cfg.model_name, cfg.dtype)
        base = AutoModelForCausalLM.from_pretrained(
            cfg.model_name, dtype=dtype, device_map=None,
        )
        base.config.use_cache = False          # incompatible with checkpointing
        if cfg.gradient_checkpointing:
            base.gradient_checkpointing_enable()
            base.enable_input_require_grads()

        self.model = get_peft_model(base, LoraConfig(
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=cfg.lora_dropout,
            target_modules=list(cfg.target_modules),
            task_type="CAUSAL_LM",
            bias="none",
        ))
        self.model.to(cfg.device)
        self.model.print_trainable_parameters()

        self.tokenizer = tokenizer
        if self.tokenizer is None:
            from rl.env import template
            self.tokenizer = template.tokenizer()
        self.pad_token_id = (
            self.tokenizer.pad_token_id
            if self.tokenizer.pad_token_id is not None
            else self.tokenizer.eos_token_id
        )

        self.optimizer = torch.optim.AdamW(
            [p for p in self.model.parameters() if p.requires_grad],
            lr=cfg.learning_rate, weight_decay=cfg.weight_decay,
        )

    # ── forward helpers ───────────────────────────────────────────
    def _logits(self, input_ids, attention_mask):
        return self.model(input_ids=input_ids, attention_mask=attention_mask).logits

    def _reference_logprobs(self, input_ids, attention_mask):
        """Token-aligned log-probs from the base model, adapter disabled.

        No second copy of the weights: LoRA leaves the base intact, so turning
        the adapter off *is* the reference policy. Under `no_grad` because
        nothing here is trained.
        """
        from rl.train import grpo

        with self.torch.no_grad(), self.model.disable_adapter():
            logits = self._logits(input_ids, attention_mask)
            return grpo.token_logprobs(logits, input_ids)

    def _sampler_logprobs(self, input_ids, attention_mask, recorded):
        """`logp_old`: the recorded values when available, else a frozen pass.

        Recorded values come from the rollout and are the correct thing — see
        `grpo.masked_token_loss`. The fallback exists for trajectories collected
        before log-probs were captured (or by the eval adapter, which does not
        capture them); it makes the ratio exactly 1 for those, so they behave
        like a first on-policy step rather than producing a garbage ratio.
        """
        if recorded is not None and recorded.abs().sum() > 0:
            return recorded
        with self.torch.no_grad():
            from rl.train import grpo
            return grpo.token_logprobs(self._logits(input_ids, attention_mask), input_ids)

    # ── the GRPO step ─────────────────────────────────────────────
    def grpo_step(self, batch, advantages: list[float], keep: list[bool],
                  grpo_config=None) -> dict:
        """One optimiser step over `batch`. Returns metrics."""
        import torch

        from rl.train import grpo

        cfg = grpo_config or grpo.GRPOConfig()
        samples = [s for s, k in zip(batch.samples, keep) if k]
        kept_adv = [a for a, k in zip(advantages, keep) if k]
        stats = StepStats(skipped=len(batch.samples) - len(samples))
        if not samples:
            return stats.to_dict()

        self.model.train()
        self.optimizer.zero_grad(set_to_none=True)

        # Total trainable tokens across the whole step, so accumulation divides
        # once rather than averaging per-micro-batch. See the module docstring.
        total_tokens = sum(sum(s.mask[1:]) for s in samples) or 1
        accumulated = {"loss": 0.0, "kl": 0.0, "entropy": 0.0,
                       "clip": 0.0, "ratio": 0.0}

        step = self.config.micro_batch_size
        for start in range(0, len(samples), step):
            chunk = samples[start:start + step]
            chunk_adv = kept_adv[start:start + step]
            ids, mask, logp_recorded = self._to_tensors(chunk)
            attention = (ids != self.pad_token_id).long()
            adv = torch.tensor(chunk_adv, dtype=torch.float, device=ids.device)

            logp_old = self._sampler_logprobs(ids, attention, logp_recorded)
            ref_logp = self._reference_logprobs(ids, attention) if cfg.kl_coef else None

            logits = self._logits(ids, attention)
            loss, micro = grpo.masked_token_loss(
                logits, ids, mask, adv, logp_old, ref_logp, cfg)

            # Re-weight: `masked_token_loss` divided by *this* micro-batch's
            # tokens, but the step's denominator is every token in the step.
            micro_tokens = float(mask[:, 1:].sum().item()) or 1.0
            (loss * (micro_tokens / total_tokens)).backward()

            share = micro_tokens / total_tokens
            accumulated["loss"] += float(micro["loss"]) * share
            accumulated["kl"] += float(micro.get("kl", 0.0)) * share
            accumulated["clip"] += float(micro["clip_fraction"]) * share
            accumulated["ratio"] += float(micro["mean_ratio"]) * share
            accumulated["entropy"] += float(grpo.entropy(logits.detach(), mask)) * share

            del logits, loss

        grad_norm = torch.nn.utils.clip_grad_norm_(
            [p for p in self.model.parameters() if p.requires_grad],
            self.config.max_grad_norm,
        )
        self.optimizer.step()

        stats.loss = accumulated["loss"]
        stats.kl = accumulated["kl"]
        stats.entropy = accumulated["entropy"]
        stats.clip_fraction = accumulated["clip"]
        stats.mean_ratio = accumulated["ratio"]
        stats.grad_norm = float(grad_norm)
        stats.trainable_tokens = int(total_tokens)
        return stats.to_dict()

    # ── the SFT step ──────────────────────────────────────────────
    def sft_step(self, samples) -> dict:
        """One optimiser step of masked cross-entropy over `samples`."""
        import torch

        from rl.train import grpo

        stats = StepStats()
        if not samples:
            return stats.to_dict()

        self.model.train()
        self.optimizer.zero_grad(set_to_none=True)
        total_tokens = sum(sum(s.mask[1:]) for s in samples) or 1
        accumulated = 0.0

        step = self.config.micro_batch_size
        for start in range(0, len(samples), step):
            chunk = samples[start:start + step]
            ids, mask, _ = self._to_tensors(chunk)
            attention = (ids != self.pad_token_id).long()
            logits = self._logits(ids, attention)
            loss = grpo.masked_cross_entropy(logits, ids, mask)

            micro_tokens = float(mask[:, 1:].sum().item()) or 1.0
            (loss * (micro_tokens / total_tokens)).backward()
            accumulated += float(loss) * (micro_tokens / total_tokens)
            del logits, loss

        grad_norm = torch.nn.utils.clip_grad_norm_(
            [p for p in self.model.parameters() if p.requires_grad],
            self.config.max_grad_norm,
        )
        self.optimizer.step()

        stats.loss = accumulated
        stats.grad_norm = float(grad_norm)
        stats.trainable_tokens = int(total_tokens)
        return stats.to_dict()

    # ── plumbing ──────────────────────────────────────────────────
    def _to_tensors(self, samples):
        """Pad a list of `pack.Sample` into (ids, mask, logp_old)."""
        import torch

        width = max(len(s) for s in samples)
        ids, masks, logps = [], [], []
        for sample in samples:
            pad = width - len(sample)
            ids.append(sample.token_ids + [self.pad_token_id] * pad)
            masks.append(sample.mask + [0] * pad)
            recorded = sample.logp_old or [0.0] * len(sample)
            logps.append(recorded + [0.0] * (width - len(recorded)))

        device = self.config.device
        return (
            torch.tensor(ids, dtype=torch.long, device=device),
            torch.tensor(masks, dtype=torch.long, device=device),
            torch.tensor(logps, dtype=torch.float, device=device),
        )

    def save_adapter(self, path: str) -> str:
        os.makedirs(path, exist_ok=True)
        self.model.save_pretrained(path)
        if self.tokenizer is not None:
            self.tokenizer.save_pretrained(path)
        return path
