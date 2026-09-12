"""Turning a session log into token ids, the same way every time.

This module exists because of one measured fact. Qwen2.5's chat template
renders a tool call as:

    {{- '", "arguments": ' }}{{- tool_call.arguments | tojson }}

and `derive_messages()` hands over `arguments` as the **raw JSON string the
model wrote** (`harness/llm/base.py:24`). Applying `tojson` to a string yields a
JSON *string*, not an object:

    as stored:  {"name": "corpus_search", "arguments": "{\\"query\\": \\"x\\"}"}
    correct:    {"name": "corpus_search", "arguments": {"query": "x"}}

vLLM's server normalises the arguments before templating, so a rollout produces
the second form while a naive local re-tokenisation produces the first. Every
tool call would differ by a handful of tokens between what the policy generated
and what the trainer thinks it generated — no error, no warning, just a
gradient computed against text that was never sampled. `normalize()` below is
the fix, and `check_prefix_property()` is the assertion that proves it worked.

Two more things are pinned here for the same reason:

- **The template is a file in this repository**, not `tokenizer_config.json`
  from the Hub. Hugging Face updates templates in place; a silent change
  mid-project would invalidate every trajectory already collected. Its sha256
  is stamped onto trajectories beside the corpus hash.
- **`<|im_end|>` is appended to every completion span.** With
  `add_generation_prompt=True` the prompt ends at `<|im_start|>assistant\\n`, and
  vLLM does not include the stop token in the completion. A model trained
  without EOS in its loss mask never learns to stop — an expensive, silent,
  classic failure.
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

from harness import events as ev
from harness.events import SessionEvent
from harness.session.projection import derive_messages

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENIZER_DIR = os.path.join(HERE, "qwen25")
TEMPLATE_FILE = os.path.join(HERE, "qwen2.5_tool.jinja")

# The tokenizer binaries are ~11 MB and re-downloadable, so they are not in git
# (see rl/.gitignore). The *template* is, because HF updates templates in place
# and a silent change would invalidate every trajectory already collected.
# A fresh clone therefore has the template but no tokenizer, and falls back to
# the Hub — `python -m rl.cli setup` pre-fetches it so a training run does not
# discover the network is unavailable at step 1.
TOKENIZER_HUB_ID = "Qwen/Qwen2.5-1.5B-Instruct"

END_OF_TURN = "<|im_end|>"


@lru_cache(maxsize=1)
def template_text() -> str:
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=1)
def template_sha256() -> str:
    return hashlib.sha256(template_text().encode("utf-8")).hexdigest()


@lru_cache(maxsize=2)
def tokenizer(path: str = TOKENIZER_DIR):
    """The tokenizer, from the local copy when present or the Hub otherwise.

    Either source gives the same vocabulary; what must not vary is the chat
    template, so the pinned file overwrites whatever came with the tokenizer in
    both cases. That assignment is the whole reason a Hub fallback is safe.
    """
    from transformers import AutoTokenizer

    source = path if os.path.isfile(os.path.join(path, "tokenizer.json")) else TOKENIZER_HUB_ID
    if source != path:
        logger.info("[template] tokenizer not in %s, falling back to %s", path, source)
    tok = AutoTokenizer.from_pretrained(source)
    # Use the pinned template, never whatever shipped with the tokenizer.
    tok.chat_template = template_text()
    return tok


def normalize(messages: list[dict]) -> list[dict]:
    """Parse every tool call's `arguments` from JSON text into an object.

    See the module docstring — this is the whole reason the module exists.
    Arguments that are not valid JSON are left as a string: the model really
    did write them that way, the registry really did reject them, and the
    trajectory should tokenise to what happened rather than to a repair.
    """
    out = []
    for message in messages:
        calls = message.get("tool_calls")
        if not calls:
            out.append(message)
            continue

        fixed_calls = []
        for call in calls:
            fn = dict(call.get("function") or {})
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    fn["arguments"] = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    pass
            fixed_calls.append({**call, "function": fn})
        out.append({**message, "tool_calls": fixed_calls})
    return out


def render(messages: list[dict], tools: list[dict] | None = None,
           add_generation_prompt: bool = False) -> str:
    return tokenizer().apply_chat_template(
        normalize(messages), tools=tools, tokenize=False,
        add_generation_prompt=add_generation_prompt,
    )


def encode(messages: list[dict], tools: list[dict] | None = None,
           add_generation_prompt: bool = False) -> list[int]:
    """Token ids, always as a flat `list[int]`.

    `apply_chat_template(tokenize=True)` returns a `BatchEncoding` in
    transformers 5.x and a bare list in 4.x. Normalising it here rather than at
    each call site matters more than it looks: a `BatchEncoding` has `len() == 2`
    (its two keys), so a prefix comparison against one silently compares key
    counts and reports every trajectory as unstable — which is exactly what it
    did before this was pinned down.
    """
    result = tokenizer().apply_chat_template(
        normalize(messages), tools=tools, tokenize=True,
        add_generation_prompt=add_generation_prompt,
    )
    ids = result["input_ids"] if hasattr(result, "keys") else result
    # Some versions batch a single conversation into a list of one.
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return list(ids)


@dataclass
class Segment:
    """One assistant turn: where its tokens sit in the full sequence."""

    start: int
    end: int

    def __len__(self) -> int:
        return self.end - self.start


@dataclass
class Packed:
    """A trajectory as the trainer wants it: one sequence, one mask."""

    token_ids: list[int] = field(default_factory=list)
    mask: list[int] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    template_sha256: str = ""

    @property
    def trainable_tokens(self) -> int:
        return sum(self.mask)

    def check(self) -> None:
        if len(self.token_ids) != len(self.mask):
            raise AssertionError("token 与 mask 长度不一致")
        if not self.segments:
            raise AssertionError("没有可训练的 assistant span")
        for seg in self.segments:
            if not all(self.mask[i] for i in range(seg.start, seg.end)):
                raise AssertionError(f"span {seg} 内有未被 mask 标记的 token")


def pack(log: list[SessionEvent], tools: list[dict] | None = None,
         system_prompt: str = "") -> Packed:
    """Tokenise one trajectory into a single sequence with a loss mask.

    For each assistant turn, the conversation *before* it (rendered with a
    generation prompt — byte-for-byte what the policy was prompted with) and the
    conversation *including* it are both tokenised. Both must be token-prefixes
    of the full sequence; the span between them is what the policy generated and
    is the only thing that gets `mask = 1`. System prompt, user turn and tool
    observations are all masked out.

    **The required property is per-assistant-turn, not per-message.** Qwen2.5's
    template merges *consecutive* tool responses into one `<|im_start|>user`
    block, so adding a second tool message moves the first block's closing
    `<|im_end|>` — mid-tool-block prefixes genuinely are not stable, and
    asserting that they were is a false alarm. No assistant turn ever sits
    inside such a block, so nothing the mask depends on is affected. Measured
    on real trajectories: every assistant prompt is an exact prefix of the full
    sequence; intermediate tool-block prefixes are not.

    A template that re-renders *earlier assistant turns* once a later one exists
    — Qwen3 does this, stripping `<think>` blocks from all but the last — would
    break the real property, and the assertions below would catch it.
    """
    messages = derive_messages(log, system_prompt)
    eos_id = tokenizer().convert_tokens_to_ids(END_OF_TURN)
    full = encode(messages, tools, add_generation_prompt=False)

    mask = [0] * len(full)
    segments: list[Segment] = []

    for i, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue

        prompt_ids = encode(messages[:i], tools, add_generation_prompt=True)
        through_ids = encode(messages[:i + 1], tools, add_generation_prompt=False)

        if full[:len(prompt_ids)] != prompt_ids:
            raise AssertionError(
                f"第 {i} 轮 assistant 的 prompt 不是完整序列的前缀——"
                f"模板会重写更早的轮次，这种 mask 构造不成立"
            )
        if full[:len(through_ids)] != through_ids:
            raise AssertionError(
                f"第 {i} 轮 assistant 连同自身也不是完整序列的前缀"
            )

        start, end = len(prompt_ids), len(through_ids)
        # The turn ends `<|im_end|>\n`; the newline is the next turn's framing.
        # The stop token itself stays *inside* the span — a policy trained
        # without EOS in its mask never learns to stop.
        while end > start and full[end - 1] != eos_id:
            end -= 1
        if end <= start:
            continue
        for pos in range(start, end):
            mask[pos] = 1
        segments.append(Segment(start, end))

    return Packed(token_ids=full, mask=mask, segments=segments,
                  template_sha256=template_sha256())


def check_prefix_property(log: list[SessionEvent], tools: list[dict] | None = None,
                          system_prompt: str = "") -> str:
    """Assert the mask construction is sound for one trajectory.

    Returns a one-line description on success and raises on failure, so it
    drops straight into the staged check script.
    """
    packed = pack(log, tools, system_prompt)
    packed.check()

    eos_id = tokenizer().convert_tokens_to_ids(END_OF_TURN)
    for seg in packed.segments:
        if packed.token_ids[seg.end - 1] != eos_id:
            raise AssertionError(
                f"span {seg} 没有以 {END_OF_TURN} 结尾——"
                f"停止符没进 loss mask，模型会学会永不停止"
            )

    return (f"{len(packed.segments)} 个 assistant span · {len(packed.token_ids)} token · "
            f"可训练 {packed.trainable_tokens}"
            f"（{packed.trainable_tokens / max(len(packed.token_ids), 1):.0%}）· 每段均含 EOS")
