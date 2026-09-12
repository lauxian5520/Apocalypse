"""Getting updated weights into the rollout server, without weight-sync code.

The obvious design — run vLLM in-process, reach into it and copy tensors after
each optimiser step — means writing and debugging weight synchronisation, and on
a single 24 GB card it means the trainer and the inference engine fighting over
memory.

The cheap alternative: vLLM runs as a **separate process** with `--enable-lora`,
the trainer saves a LoRA adapter to disk, and this module tells the server to
load it. The base weights never move. There is no synchronisation code because
there is nothing to synchronise — the adapter is a few dozen MB of files and the
server reads them itself.

What that buys, concretely:

- No VRAM contention: the trainer can hold optimiser state while the server
  holds KV cache, each in its own process with its own allocator.
- The *same* `PolicyAdapter` serves rollout during training and evaluation
  afterwards, so a trained checkpoint is evaluated through the identical code
  path it was trained through.
- A failed step is recoverable: the previous adapter is still on disk and still
  loadable.

The cost is that each swap is a file write plus an HTTP round trip. At one swap
per optimiser step over a few hundred steps that is negligible next to rollout.

**Requires `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1` on the server.** Without it the
endpoints return 404 and the whole scheme silently degrades to "training a model
nobody is sampling from" — so `check_runtime_lora()` asks up front rather than
letting a run discover it at step 1.
"""
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
# A swap writes files then asks the server to read them; on a cold page cache
# the read can lag the write.
LOAD_RETRIES = 3
LOAD_BACKOFF_SECONDS = 2.0


@dataclass
class VLLMServer:
    """A handle on a running vLLM server, for LoRA swaps and health checks."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self):
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.root = base
        self.v1 = f"{base}/v1"

    # ── URLs, kept in one place so a path change is a one-line edit ──
    @property
    def models_url(self) -> str:
        return f"{self.v1}/models"

    @property
    def load_url(self) -> str:
        return f"{self.v1}/load_lora_adapter"

    @property
    def unload_url(self) -> str:
        return f"{self.v1}/unload_lora_adapter"

    # ── operations ────────────────────────────────────────────────
    def wait_ready(self, deadline_seconds: float = 600.0, poll: float = 3.0) -> list[str]:
        """Block until the server answers, returning the model ids it serves."""
        started = time.monotonic()
        last: Exception | None = None
        while time.monotonic() - started < deadline_seconds:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(self.models_url)
                if response.status_code == 200:
                    return [m.get("id", "") for m in (response.json().get("data") or [])]
                last = RuntimeError(f"HTTP {response.status_code}")
            except httpx.HTTPError as e:
                last = e
            time.sleep(poll)
        raise RuntimeError(f"vLLM 在 {deadline_seconds:.0f}s 内没有就绪：{last}")

    def check_runtime_lora(self) -> None:
        """Fail now if runtime LoRA updating is off.

        A 404 here means `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1` was not set. Left
        undetected, every swap silently no-ops and the run samples from the base
        model for its whole duration while the loss curve moves — the reward
        never does, and the cause is invisible.
        """
        try:
            with httpx.Client(timeout=10.0) as client:
                # Deliberately malformed: a server with the feature enabled
                # rejects it with 4xx-but-not-404; one without it has no route.
                response = client.post(self.load_url, json={})
        except httpx.HTTPError as e:
            raise RuntimeError(f"无法访问 {self.load_url}：{e}") from e

        if response.status_code == 404:
            raise RuntimeError(
                "vLLM 没有开启运行时 LoRA 热插拔（load_lora_adapter 返回 404）。"
                "启动时需要 VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 与 --enable-lora。"
                "不开的话每次换权重都会静默失效，训练全程都在采样基座模型。"
            )

    def load(self, name: str, path: str) -> None:
        """Point `name` at the adapter in `path`, replacing any previous one."""
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise FileNotFoundError(f"LoRA 目录不存在：{path}")

        # Unloading first is what makes a swap idempotent: loading a name the
        # server already knows is an error on some builds, and a half-completed
        # swap that left the old adapter in place would train against one
        # version while sampling from another.
        self.unload(name, missing_ok=True)

        last: Exception | None = None
        for attempt in range(LOAD_RETRIES):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self.load_url,
                        json={"lora_name": name, "lora_path": path},
                    )
                if response.status_code < 300:
                    logger.info("[lora] loaded %s from %s", name, path)
                    return
                last = RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            except httpx.HTTPError as e:
                last = e
            time.sleep(LOAD_BACKOFF_SECONDS * (attempt + 1))
        raise RuntimeError(f"加载 LoRA {name} 失败：{last}")

    def unload(self, name: str, missing_ok: bool = False) -> None:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.unload_url, json={"lora_name": name})
        except httpx.HTTPError as e:
            if missing_ok:
                return
            raise RuntimeError(f"卸载 LoRA {name} 失败：{e}") from e
        if response.status_code >= 300 and not missing_ok:
            raise RuntimeError(f"卸载 LoRA {name} 失败：HTTP {response.status_code}")


def adapter_name(step: int) -> str:
    """The name a given training step's adapter is served under.

    Step-numbered rather than a fixed name so a trajectory's `stamp` can record
    which policy version produced it. Off-policy data mixed in unknowingly is
    one of the harder RL bugs to see, and a version in the record makes it
    checkable instead of guessable.
    """
    return f"policy-step-{step:06d}"


def adapter_dir(root: str, step: int) -> str:
    return os.path.join(root, adapter_name(step))


def save_and_swap(model, tokenizer, server: VLLMServer, root: str, step: int) -> str:
    """Save the current LoRA adapter and make the server serve it.

    Returns the name the server now knows it by, which belongs in the run
    record and in every trajectory sampled afterwards.
    """
    path = adapter_dir(root, step)
    os.makedirs(path, exist_ok=True)
    model.save_pretrained(path)
    if tokenizer is not None:
        tokenizer.save_pretrained(path)

    name = adapter_name(step)
    server.load(name, path)
    return name
