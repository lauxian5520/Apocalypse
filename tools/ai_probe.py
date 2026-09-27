"""Check that the configured AI provider is reachable and answering.

    cd backend && python ../tools/ai_probe.py
"""
import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core.providers import provider_config  # noqa: E402
from services import ai_service  # noqa: E402

PROMPT = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "只回复 OK"},
]


async def main() -> int:
    cfg = provider_config()
    print(f"provider = {cfg['model']} @ {cfg['url']}")
    print(f"api key  = {'set' if cfg['key'] else 'MISSING'}")

    failed = False
    try:
        reply = await ai_service.chat(PROMPT)
        print("blocking  OK:", reply[:120])
    except Exception:
        failed = True
        print("blocking  FAILED")
        traceback.print_exc()

    try:
        chunks: list[str] = []
        async for chunk in ai_service.chat_stream(PROMPT):
            chunks.append(chunk)
            if len("".join(chunks)) > 50:
                break
        print("streaming OK:", "".join(chunks)[:120])
    except Exception:
        failed = True
        print("streaming FAILED")
        traceback.print_exc()

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
