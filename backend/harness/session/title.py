"""Automatic session titles.

One cheap non-streaming call after the first exchange, so the sidebar shows
something better than a timestamp. Failure is silent by design: a session
without a title is a cosmetic problem, not a broken run.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).resolve().parent.parent / "data" / "prompts" / "title.md"
MAX_TITLE_CHARS = 40
# Far larger than the title itself needs: a thinking model (deepseek-v4-pro and
# friends) consumes this budget on reasoning before writing a single visible
# character, and a tight cap returns nothing at all.
TITLE_MAX_TOKENS = 512


async def generate_title(llm, first_message: str) -> str:
    """A short title for a session, or "" when one cannot be produced."""
    if not first_message.strip():
        return ""
    try:
        instruction = PROMPT_FILE.read_text(encoding="utf-8").strip()
        result = await llm.complete(
            [{"role": "user", "content": f"{instruction}\n\n---\n\n{first_message[:1000]}"}],
            max_tokens=TITLE_MAX_TOKENS,
        )
    except Exception as e:
        logger.info("[harness] title generation skipped: %s", e)
        return ""

    # Models like to wrap a title in quotes or trail a period despite being
    # told not to; strip rather than re-prompt.
    title = result.content.strip().strip("\"'「」《》 \n\t.。")
    return title[:MAX_TITLE_CHARS]
