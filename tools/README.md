# tools/

Development helpers. Not imported by the application and not copied into the
Docker image — run them by hand from the repository root.

| Script | What it does |
|--------|--------------|
| `ai_probe.py` | Calls the configured AI provider directly (blocking + streaming) to check that the key, URL and model in `.env` actually work. |
| `check_scrapers.py` | Runs the GitHub / HuggingFace / papers scrapers once and prints what came back. |
| `check_feeds.py` | Refreshes the hot-topic feed and summarises the categories written to `var/feeds/focus.json`. |
| `harness_check.py` | Connectivity self-check for the Harness subsystem: config, database, sandbox, tool registry, approval policy, event-log projection, the live provider, and optionally the HTTP + SSE layer. Each stage runs independently and exits non-zero if any fail. |
| `harness_probe.py` | Drives one complete Harness turn with no browser and no server, printing the event log, the projected messages, token usage and the resulting workspace files. |

```bash
cd backend && python ../tools/ai_probe.py

# Harness: local wiring only (no tokens spent)
cd backend && python ../tools/harness_check.py --offline

# Harness: everything, including real provider calls
cd backend && python ../tools/harness_check.py

# Harness: also check a running server's HTTP + SSE layer
cd backend && python ../tools/harness_check.py --url http://localhost:8000 --token <admin JWT>
```
