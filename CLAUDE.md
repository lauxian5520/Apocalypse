# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

天启 Apocalypse — a personal knowledge space: FastAPI backend, vanilla-JS frontend, SQLite by
default. Chinese for anything a person reads at runtime — error messages, UI text, the model
prompts in `harness/data/prompts/*.md`, and the README. English for code: identifiers, comments,
and the `_comment` keys inside JSON data files.

## Commands

```bash
# Local dev — the backend also serves the frontend, so this is the whole stack on :8000
cd backend && pip install -r requirements.txt && python main.py

# Docker (production shape: nginx in front, ./var as the single state volume)
docker compose up -d

# Swagger at /docs, health at /healthz
```

There is **no test framework** (no pytest in `requirements.txt`). Verification is done with the
probe scripts in `tools/`, run from `backend/` so imports resolve:

```bash
cd backend
python ../tools/ai_probe.py                    # is the configured LLM key/URL/model working
python ../tools/harness_check.py --offline     # Harness local wiring, spends no tokens
python ../tools/harness_check.py               # + real provider calls
python ../tools/harness_check.py --url http://localhost:8000 --token <admin JWT>   # + HTTP/SSE
python ../tools/harness_probe.py --prompt "…"  # one full agent turn, no browser
```

`harness_check.py` runs each stage independently and exits non-zero on any failure, so it works
in a deploy script. Prefer adding a stage there over writing a one-off script.

## Layering

```
routers  →  services | harness  →  models
    └──────────┴─────────┴──────────┴────→  core
```

Enforced by convention, not tooling — respect it:

- `core/` imports no business module.
- `services/` and `harness/` never import `routers`, never touch `Request`/`HTTPException`. They
  raise the domain exceptions in `core/errors.py`; `main.py`'s `AppError` handler maps them to
  status codes. `core/deps.py` is the one exception — it *is* the HTTP layer.
- `harness/` is a self-contained subsystem sitting at the `services/` layer.

## Things that will bite you

- **`routers/__init__.py` is the single definition of the URL map.** `main.py` never lists routes.
  A new router = one file + one `include_router` line there.
- **The static mount in `main.py` must stay last.** A `Mount` on `/` matches everything and would
  shadow any route declared after it.
- **A new table needs nothing in `models/migrations.py`** — `Base.metadata.create_all` handles it.
  `_ADDED_COLUMNS` there is *only* for columns added to tables that already exist in deployed
  databases. But a new model **must** be imported in `models/__init__.py` or its mapper never
  registers.
- **Adding a runtime directory**: add the field to `Settings`, add its default to the dict in
  `_resolve_runtime_paths()`, and append it to `runtime_dirs` (which `lifespan` creates).
  Everything derives from the single `VAR_DIR` knob.
- **SSE frame format is load-bearing.** `core/sse.py` emits `data: ` *with the space*; the browser
  does `line.slice(6)`. Don't "tidy" it. Errors after headers are flushed travel in-band as
  `{"error": …}`, never as a status code.
- **Python 3.10+** is gated at import time in `main.py`.
- **The first registered account becomes admin** (`routers/auth.py`).
- LLM traffic is hand-rolled `httpx` against the OpenAI-compatible `/chat/completions` shape —
  there is no `openai` SDK. `sse-starlette`, `alembic` and `zai-sdk` are in `requirements.txt` but
  unused.

## Frontend conventions

- HTML pages carry **no inline JS/CSS**. Core scripts load in a fixed order:
  `utils → auth → api → ui`.
- **BOM usage is inconsistent** — most `.html` files have one (`messages`/`profile` don't), and of
  the assets only `css/base.css` and `js/widgets/sprite-chat.js` do. Preserve whatever a file
  already has when editing; don't add one to a new JS/CSS file.
- **The nav is copy-pasted into every page, not templated.** A new page means editing each existing
  page's `.nav-links`. Note they are not uniform: `admin/messages/profile` have deliberately
  shorter navs, and `login/register` have none.
- `base.css` does **not** provide `body { padding-top: 80px }` or `.page-wrap` — each
  `css/pages/*.css` declares them itself.
- **The JWT is HttpOnly and unreadable from JS.** `Auth.token()` returns the sentinel string
  `'__cookie__'` so `if (Auth.token())` reads naturally — it is not a credential. Use `apiFetch()`
  (adds the CSRF header automatically); it returns JSON only, so **streaming endpoints need a bare
  `fetch`** with `credentials: 'include'` and a manual `X-CSRF-Token`.
- `ui.js` injects the floating sprite assistant into every page. Pages that own their own
  conversation opt out via `CHAT_HOSTING_PAGES` in `js/widgets/sprite-chat.js`.

## The Harness subsystem (`backend/harness/`)

An agent workbench modelled on the ideas of deepseek-ai/deepseek-harness (not a port). Two
invariants hold the design together:

1. **Replaceable seams.** `SessionStore`, `ModelAdapter`, `Sandbox`, `ToolRegistry` are each a
   protocol plus a default implementation, all selected in `harness/context.py`. Nothing else knows
   which implementation it got.
2. **Everything the model sees is logged first.** `harness/session/projection.py::derive_messages()`
   is the *only* function permitted to turn the log into a request. The loop writes each event to
   the store **before** yielding it. Keep both properties when changing the loop.

`loop/agent.py` produces `SessionEvent`s and knows nothing about HTTP; SSE framing is the router's
job. Only `user/message`, `assistant/message` and `tool/result` project into model messages —
everything else is log-only.

**Data/code separation** is concrete here: a tool's model-facing contract (name, description, JSON
Schema, permission) lives in `harness/data/tools/*.json`; Python supplies only the handler, exported
via a `HANDLERS` dict and bound by name at load time. A contract with no matching handler raises
the first time a `ToolRegistry` is built, rather than mid-conversation.
Prompts, presets, the price table and the shell allowlist are likewise data files. **Adding a tool =
edit one JSON file + add a handler; no other code changes.**

Non-obvious behaviour learned the hard way, all commented at the relevant code:

- **Streaming cleanup must not live in the response generator's `finally`.** A client that closes
  its tab leaves the generator suspended and Python may run that `finally` much later or never.
  Cleanup is a Starlette `BackgroundTask`, and `manager.reconcile_status()` heals the row from the
  log as a backstop.
- **Status is derived per turn**, not from the single last event — the loop emits `step/end` *after*
  `tool/approval`.
- **Compaction triggers on the provider's real `prompt_tokens`** (from `llm/usage`), not a local
  estimate: every request also carries the system prompt and all tool schemas (~1500–2000 tokens),
  which a text-only estimate misses entirely. It refuses to summarise when the gain would be
  trivial, so a too-low budget warns instead of looping.
- **Thinking models (`deepseek-v4-pro`) spend the output budget on reasoning before emitting text.**
  Short-output calls need generous `max_tokens`; `complete()` raises rather than returning an empty
  string when the cap is hit, because a silent `""` hid a broken feature.
- **Streamed chunks are batched** (`CHUNK_FLUSH_SIZE` / `CHUNK_FLUSH_SECONDS`) — one commit per
  token cost roughly a fifth of a long turn's wall clock.
- The system prompt carries the current **date only**; second precision would invalidate the
  provider's prefix cache on every request.

### Security posture — read before loosening anything

The sandbox is a fence, not a jail. It guards against the agent doing something unintended (prompt
injection in fetched content, a misread instruction), not against a malicious operator. Defaults are
the safe ones and should stay that way: `HARNESS_REQUIRE_ADMIN=true`, `HARNESS_SHELL_ENABLED=false`.

- Commands run via `execve` on parsed argv — **never through a shell**. Bare shell operators are
  rejected; a quoted `;` is fine because nothing interprets it.
- `data/shell_allowlist.json` auto-approves only commands that can neither execute arbitrary code
  nor open a socket. `python`, `node`, `git`, `curl`, `find`, `awk` deliberately require approval —
  that is the feature working, not a gap to close.
- All file paths pass `services/storage_service.py::contained_path()`, shared with public uploads.
  Fix path logic there, once.

## README

`README.md` is the user-facing document and is kept current — update it alongside behaviour changes,
especially the config table and the Harness security section.
