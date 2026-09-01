"""Run each scraper once and report what it returned.

    cd backend && python ../tools/check_scrapers.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from scrapers import github_trending, hf_papers, huggingface  # noqa: E402


async def main() -> None:
    repos = await github_trending.fetch("daily")
    print(f"GitHub    : {len(repos):3} items  first={repos[0].get('name') if repos else '—'}")

    papers = await hf_papers.fetch()
    print(f"HF papers : {len(papers):3} items  first={(papers[0].get('title', '')[:60]) if papers else '—'}")

    models = await huggingface.fetch(5)
    print(f"HF models : {len(models):3} items  first={models[0].get('name') if models else '—'}")


if __name__ == "__main__":
    asyncio.run(main())
