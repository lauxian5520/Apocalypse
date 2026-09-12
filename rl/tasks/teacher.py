"""The teacher: a plain blocking client used by the synthesis pipeline.

Deliberately *not* the harness `ModelAdapter`. That seam exists to let the agent
loop talk to a provider; this is a one-shot "rewrite this paragraph" helper with
no tools, no streaming and no event log. Conflating them would drag the whole
session machinery into a pipeline that only ever needs request-in, text-out.

Provider selection is reused, though — `core.providers.provider_config()` is the
same resolution the website and the harness use, so the synthesis pipeline
honours `AI_PROVIDER` and the per-provider key/url/model triple without a second
configuration surface.

One provider-specific hazard is handled here because the repository has been
bitten by it before: a *thinking* model — most of the DeepSeek line, including
whatever `DEEPSEEK_MODEL` currently names — spends its output budget on
reasoning before emitting a visible character. A modest `max_tokens` therefore returns an empty string rather than
a short answer. This client raises on an empty body instead of returning `""`,
for the same reason `harness/llm/openai_compatible.py::complete()` does — a
silent empty string hides a broken feature.
"""
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.providers import provider_config, require_configured  # noqa: E402

# Generous by default: see the module docstring. A thinking model burns most of
# this before its first visible token.
DEFAULT_MAX_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 180
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5.0
# Conservative: a sweep that gets the key rate-limited is not faster, and these
# batches are minutes-long background jobs, not anything interactive.
DEFAULT_CONCURRENCY = 8
# Above this share of failed prompts a batch is treated as an outage and raises,
# rather than returning placeholders that look like real (wrong) answers.
MAX_ERROR_FRACTION = 0.2

logger = logging.getLogger(__name__)


class TeacherError(RuntimeError):
    """Something went wrong talking to the provider."""


class EmptyResponseError(TeacherError):
    """The call succeeded but the model emitted no visible text.

    A distinct class because it is a *different kind* of failure from an outage,
    and `ask_many` has to treat them differently. A thinking model that spends
    its whole output budget on reasoning is behaving normally at a measurable
    rate (measured: ~18-22% for `deepseek-v4-flash` at a 4096 cap), and the
    caller records it as "no verdict" and moves on. An HTTP 429 or an
    unreachable endpoint means the *run* is broken and should stop.

    Conflating them cost a 4,834-question sweep: the abort threshold counted
    empty bodies, so a perfectly healthy batch with a slightly-above-average
    tail tripped it and killed the job at question 1,000.
    """


class Teacher:
    """One configured model, called synchronously."""

    def __init__(self, model: str = "", temperature: float = 0.7):
        self.cfg = provider_config()
        require_configured(self.cfg)
        self.model = model or self.cfg["model"]
        self.temperature = temperature
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def ask(self, prompt: str, *, system: str = "", max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = self._post(payload)

        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

        if not content.strip():
            finish = choice.get("finish_reason", "")
            raise EmptyResponseError(
                f"模型返回空正文（finish_reason={finish!r}）。"
                f"思考型模型会先花输出预算推理，请调大 max_tokens（当前 {max_tokens}）"
            )
        return content.strip()

    def ask_json(self, prompt: str, *, system: str = "", max_tokens: int = DEFAULT_MAX_TOKENS) -> dict | list:
        """Ask for JSON and parse it, tolerating a fenced code block."""
        raw = self.ask(prompt, system=system, max_tokens=max_tokens)
        text = raw.strip()
        if text.startswith("```"):
            # Strip ```json ... ``` fencing, which models add unbidden.
            text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[: -3]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            raise TeacherError(f"模型没有返回合法 JSON（{e}）：{raw[:200]}") from e

    def _post(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.cfg.get("key"):
            headers["Authorization"] = f"Bearer {self.cfg['key']}"

        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            req = urllib.request.Request(self.cfg["url"], data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last = TeacherError(f"HTTP {e.code}: {detail}")
                if e.code < 500 and e.code != 429:
                    raise last from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise TeacherError(f"provider 不可达，重试 {MAX_RETRIES} 次后放弃：{last}")

    def ask_many(
        self,
        prompts: list[str],
        *,
        system: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        concurrency: int = DEFAULT_CONCURRENCY,
        on_error: str | None = "",
    ) -> list[str]:
        """`ask` over a batch, in parallel, results in input order.

        A thread pool rather than asyncio because `ask` is a blocking
        `urllib` call and this is an offline batch job — there is no event loop
        to share and no benefit to rewriting the client around one.

        `on_error` is the placeholder for a prompt that failed after retries;
        `None` re-raises instead. Tolerating a few failures matters for a sweep:
        one 500 from the provider should not discard 199 good answers.

        **But tolerance has a ceiling, and only for the right failures.** An
        early version returned the placeholder unconditionally, and when the
        endpoint began 404ing it produced 7,405 rows of empty answers scored as
        "the model knew nothing" — a completed-looking file built from zero
        successful calls. So past `MAX_ERROR_FRACTION` transport failures this
        raises.
        **Empty bodies are excluded from that count** (`EmptyResponseError`):
        they are an expected outcome at a known rate, the caller records them as
        "no verdict", and counting them killed a healthy sweep at question
        1,000 the first time round.

        The counters are updated with `+=` on ints, which is not atomic, so
        usage totals are approximate under concurrency. They are only ever used
        for reporting spend.
        """
        from concurrent.futures import ThreadPoolExecutor

        # Two counters, because the two failure classes mean different things —
        # see `EmptyResponseError`. Only transport failures can abort a run.
        empty: list[str] = []
        failures: list[str] = []

        def one(prompt: str) -> str:
            try:
                return self.ask(prompt, system=system, max_tokens=max_tokens)
            except EmptyResponseError as e:
                if on_error is None:
                    raise
                empty.append(str(e))
                return on_error
            except Exception as e:
                if on_error is None:
                    raise
                failures.append(f"{type(e).__name__}: {e}")
                return on_error

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            answers = list(pool.map(one, prompts))

        if empty:
            logger.info(
                "[teacher] %d/%d prompts returned no visible text (recorded as unknown)",
                len(empty), len(prompts),
            )

        if prompts and len(failures) / len(prompts) > MAX_ERROR_FRACTION:
            # State what happened, not why. The first time this fired the cause
            # was not the provider at all — it was `max_tokens` set too low for
            # a thinking model, so every slow question returned an empty body.
            # An error that names a cause it has not established sends the
            # reader to the wrong place.
            raise TeacherError(
                f"本批 {len(prompts)} 条里有 {len(failures)} 条失败"
                f"（超过 {MAX_ERROR_FRACTION:.0%}），这批结果不可信，已中止。"
                f"首个错误：{failures[0][:200]}"
            )
        if failures:
            logger.warning(
                "[teacher] %d/%d prompts failed; first: %s",
                len(failures), len(prompts), failures[0][:200],
            )
        return answers

    def usage(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
