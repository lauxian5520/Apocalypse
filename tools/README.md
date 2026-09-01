# tools/

Development helpers. Not imported by the application and not copied into the
Docker image — run them by hand from the repository root.

| Script | What it does |
|--------|--------------|
| `ai_probe.py` | Calls the configured AI provider directly (blocking + streaming) to check that the key, URL and model in `.env` actually work. |
| `check_scrapers.py` | Runs the GitHub / HuggingFace / papers scrapers once and prints what came back. |
| `check_feeds.py` | Refreshes the hot-topic feed and summarises the categories written to `var/feeds/focus.json`. |

```bash
cd backend && python ../tools/ai_probe.py
```
