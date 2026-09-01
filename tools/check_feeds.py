"""Refresh the hot-topic feed and summarise what was cached.

    cd backend && python ../tools/check_feeds.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.feed_service import feed_path, refresh_focus  # noqa: E402


async def main() -> None:
    print("Refreshing focus feed…")
    await refresh_focus()

    with open(feed_path("focus"), encoding="utf-8") as f:
        data = json.load(f)

    for category, payload in (data.get("categories") or {}).items():
        sections = payload.get("sections", {})
        total = sum(
            sum(len(section.get("items", [])) for section in group)
            for group in sections.values()
        )
        print(f"  {category:10} {len(sections):2} sources  ~{total} items")


if __name__ == "__main__":
    asyncio.run(main())
