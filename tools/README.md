# tools/

Development helpers. Not imported by the application and not copied into the
Docker image — run them by hand from the repository root.

| Script | What it does |
|--------|--------------|
| `ai_probe.py` | Calls the configured AI provider directly (blocking + streaming) to check that the key, URL and model in `.env` actually work. |
| `check_scrapers.py` | Runs the GitHub / HuggingFace / papers scrapers once and prints what came back. |
| `check_feeds.py` | Refreshes news and reports category counts. `--offline` runs deterministic RSS/Atom date filtering, stale/missing cache recovery, concurrent refresh, outage preservation, paper-classification invalidation, official HF period queries and scheduled conference refreshes without page visits. |
| `conference_translation_check.py` | Isolated checks for the 15-conference registry, proceedings adapters, alternating conference years, filtering, caching and authenticated LLM translation (currently DeepSeek). Add `--browser` for desktop/mobile conference selection and translation UI checks (requires Playwright and `/usr/bin/chromium`). Uses synthetic feeds and a local mock model. |
| `toolbox_check.py` | Desktop/mobile browser acceptance for the five local utilities, clipboard/download, image transparency and URL cleanup, line limits, and existing tools/translation. Run `.venv/bin/python tools/toolbox_check.py`; requires Playwright and `/usr/bin/chromium`, and uses an isolated static server with mocked APIs. |
| `memo_search_check.py` | Isolated real-API checks for memo keyword search, literal wildcard characters, pagination, anonymity and private access. Add `--browser` for desktop/mobile search, debounce, Chinese input, clearing and request ordering (requires Playwright and `/usr/bin/chromium`). |
| `harness_check.py` | Connectivity self-check for the Harness subsystem: config, database, sandbox, tool registry, tool auto-discovery, skills, subagent wiring, artefact download (incl. packaged-skill install), approval policy, event-log projection, the live provider, and optionally the HTTP + SSE layer. Each stage runs independently and exits non-zero if any fail. |
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

Delivery commands: `release_check.py --group web|rl|all` runs isolated gates;
`live_check.py` is the separately triggered 10-request acceptance;
`backup.py create|verify|restore|prune` manages offline SQLite archives;
`package_rl.py` audits and packages existing experiments. See `docs/DELIVERY.md`.

`rl_inputs_check.py` 离线校验语料与两套 tokenizer/模板指纹；`package_candidate.py` 导出已审阅的 Git 暂存区快照、tree ID 与 sha256，不创建提交。先暂存完整候选代码，再运行 `python tools/package_candidate.py`；任何未暂存或未审阅的新文件都会阻止打包。
