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

The RL workbench in `rl/` has its own staged check and its own CLI. Both run **from the
repository root**, not from `backend/` — the opposite of `tools/`:

```bash
python -m rl.checks.rl_check --offline    # 19 stages, no tokens; 20 with a real rollout
python -m rl.cli corpus hotpot --split validation && python -m rl.cli corpus index
python -m rl.cli corpus verify            # re-hash docs.jsonl against its manifest
python -m rl.cli tasks leakage --all --model deepseek-chat --resume --out data/leak.jsonl
python -m rl.cli tasks split --require-coverage --leakage-file A.jsonl --leakage-file B.jsonl
python -m rl.cli rollout --split dev -n 50 --out data/traj.jsonl --report out.md
python -m rl.cli export data/traj.jsonl --format tokens --out data/verl.jsonl

# training — the only part that needs a GPU. Both entry points preflight their
# dependencies and exit with an actionable message rather than a traceback.
python -m rl.train.run_sft collect --split train -n 400 -G 4   # no GPU
python -m rl.train.run_sft train --base Qwen/Qwen2.5-1.5B-Instruct
python -m rl.train.run_grpo --smoke                            # 2 questions, 1 step
```

**Two Python environments, on purpose.** `rl/` needs a tokenizer at rollout and eval time but
torch only for training, and torch is not installable on this machine's default interpreter:

| | version | has |
|---|---|---|
| default `python3` | conda 3.14 | `transformers`, `tokenizers`, `sqlalchemy`, `pyarrow`, `socksio`, `numpy` — everything except training |
| `~/miniconda3/envs/py311` | 3.11 | `torch` (CPU) |

So the corpus, task, verifier, rollout, export and mask stages run under the default
interpreter, and the two torch-dependent check stages (`GRPO 损失`, `两种 advantage 实现一致`)
are **skipped there and run under `py311`**. A GPU box installs
`rl/requirements.txt` + `rl/requirements-trainer.txt` into one 3.11/3.12 env and gets all of it.

## External binaries

Beyond `requirements.txt`, a few paths shell out. Each degrades with a clear message rather than a
traceback, so a missing one is a reduced feature, not a broken deploy:

| Used by | Needs | Where it comes from |
|---|---|---|
| `skills/docx/scripts/read_docx.py` | `pandoc` | `pypandoc_binary` in `requirements.txt` bundles the binary; the script falls back to `pypandoc.get_pandoc_path()` when `pandoc` is not on PATH, which is what makes it work in Docker |
| `skills/pptx/scripts/thumbnail.py` | `soffice`, `pdftoppm` | **dev machine only — the image installs neither** |

The image also carries no CJK font (`fonts-dejavu-core` has no Han glyphs), so adding LibreOffice
alone would still render a Chinese deck as tofu. Generated `.docx`/`.pptx` are unaffected: they
embed no fonts and render on the viewer's machine.

## Layering

```
rl/  ─→  routers  →  services | harness  →  models
              └──────────┴─────────┴──────────┴────→  core
```

Enforced by convention, not tooling — respect it:

- `core/` imports no business module.
- `services/` and `harness/` never import `routers`, never touch `Request`/`HTTPException`. They
  raise the domain exceptions in `core/errors.py`; `main.py`'s `AppError` handler maps them to
  status codes. `core/deps.py` is the one exception — it *is* the HTTP layer.
- `harness/` is a self-contained subsystem sitting at the `services/` layer.
- `rl/` sits outside `backend/` entirely and depends inward on `harness/`. Nothing under
  `backend/` may import it — that is what keeps torch out of the web server's import graph.

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
- **`_MODULE_GATES` in `harness/tools/registry.py` kills a whole tool module by setting.**
  A gated-off module disappears from every preset at once, so a preset asking for it never
  overrides the deployment's answer. This is how `corpus` stays out of the live site: `standard`
  is `tools: ["*"]`, so without the gate the three `corpus_*` tools would appear the moment the
  contract file exists, and the provider's prefix cache would invalidate once. Default is off.
- **`rl/` depends on `backend/harness`; `backend/` must never import `rl/`.** The corpus reader
  and BM25 index live in `harness/corpus/` because they define what the agent *sees*; the
  training and evaluation machinery wrapped around them lives in `rl/`. A dependency the other
  way would put torch in the web server's import graph. See `rl/README.md`.
- **Python 3.10+** is gated at import time in `main.py`.
- **The first registered account becomes admin** (`routers/auth.py`).
- **The sprite's persona lives in `backend/data/prompts/sprite.md`**, not in Python, and is
  prepended in `routers/ai.py` *after* the history trim so a long chat cannot push it out. It is
  read from disk per request, so editing it takes effect on the next message. `summarize` and
  `explain_memo` deliberately keep their own task prompts — a work product should not perform.
- LLM traffic is hand-rolled `httpx` against the OpenAI-compatible `/chat/completions` shape —
  there is no `openai` SDK. `sse-starlette`, `alembic` and `zai-sdk` are in `requirements.txt` but
  unused.

## Frontend conventions

- HTML pages carry **no inline JS/CSS**. Core scripts load in a fixed order:
  `utils → auth → api → ui`.
- **BOM usage is inconsistent** — most `.html` files have one (`messages`/`profile` don't); among
  the assets only `css/base.css` does. Preserve whatever a file already has when editing, and know
  that rewriting a file wholesale (Write rather than Edit) silently drops it — check with
  `head -c3 <file> | od -An -tx1` when it matters. Don't add one to a new JS/CSS file.
- **The nav is copy-pasted into every page, not templated.** A new page means editing each existing
  page's `.nav-links`. Note they are not uniform: `admin/messages/profile` have deliberately
  shorter navs, and `login/register` have none.
- `base.css` does **not** provide `body { padding-top: 80px }` or `.page-wrap` — each
  `css/pages/*.css` declares them itself.
- **The Harness workbench is sized to exactly one viewport**, and the sum of everything above
  and below it lives once in `--hs-chrome` (`harness.css`). It was a hand-tuned `110px` against
  a real 128px, which is a permanent 18px scrollbar whose cause is invisible. Keep it derived
  from the same literals. For the same reason a pane's `min-height` must never exceed the grid
  track it sits in: a floor bigger than its track does not win room, it overflows downward
  across whatever is below — which is why `.hs-conversation` releases its floor under
  `@media (max-height: 620px)`.
- **The JWT is HttpOnly and unreadable from JS.** `Auth.token()` returns the sentinel string
  `'__cookie__'` so `if (Auth.token())` reads naturally — it is not a credential. Use `apiFetch()`
  (adds the CSRF header automatically); it returns JSON only, so **streaming endpoints need a bare
  `fetch`** with `credentials: 'include'` and a manual `X-CSRF-Token`.
- `ui.js` injects the floating sprite assistant into every page. `CHAT_HOSTING_PAGES` in
  `js/widgets/sprite-chat.js` opts a page out, and `messages.html` is the only member: a DM is
  addressed to a person, so a second box answering as 天启 reads as that person replying.
  `harness.html` was on the list too and should not have been — a harness session is a different
  agent, not the same conversation twice, and opting out left the sprite there inert (the click
  handler returns early, so nothing at all happens). Adding a page here makes its sprite dead,
  not just quiet.
- **Position the chat dialog with `offsetWidth`/`offsetHeight`, never `getBoundingClientRect()`.**
  It owns a `transform: scale()` open transition, so a rect measured while that runs is the
  *scaled* size. It is also opened nearly empty and grows as the reply streams in, which is why a
  `ResizeObserver` re-anchors it: pinning `top` once at open time dropped the finished answer and
  the whole input row below the fold, where the scroll-to-bottom then parked the newest text.
- **`index.js` scroll-jacks the whole window** to drive the time-rift sections, so its `wheel`,
  `keydown` and `touchstart` handlers all skip events that land on a floating overlay
  (`OVERLAYS` there). Without that guard, wheeling through 天启's reply scrolled the reply *and*
  flipped the page behind it, and an arrow key typed into the chat input moved the page.
- **The AI 总结 buttons on the feed pages are bound once, by delegation, in `feeds.js`.** Every
  renderer emits them through `summarizeButton()` and no loader binds anything. It used to be the
  other way round — the markup carried an inline `onclick` that called `stopPropagation`, so a
  delegated listener was impossible and each loader had to bind the buttons itself. `loadFeed`
  did; `loadClassifiedFeed` (the trending page's loader) did not, so 热门项目 rendered buttons
  that did nothing at all. Don't reintroduce a per-loader binding: the delegated handler cancels
  the click itself, which is what stops the surrounding `<a class="feed-card">` from navigating.
  Note `focus.html` does not load `feeds.js`, so `renderFocusCard` there is currently dead.
- **Never re-render the Harness inspector per streamed event.** `renderTab()` rebuilds the whole
  log; calling it from the SSE loop is quadratic, and a streamed turn measured 24ms *per event*
  and 30s of blocked main thread by its 2000th — chunks arrive every 32 characters, far faster
  than that, so the page simply stopped responding. The live path calls `T.appendEvents()`, which
  draws only what is new (2000 events: 30.2s → 0.05s). That is also why row clicks are delegated
  to the container with `__events`/`__onFork` parked on it: per-row listeners force every render
  to be a full rebuild.
- **A Harness turn outlives the page that started it.** Reloading mid-turn used to leave 中断
  hidden (only `runStream` revealed it) while the server rejected every send with `is_busy` —
  no way out from the UI, which is why refreshing appeared not to help. `openSession` now adopts
  the server's status and polls until the turn ends. `state.ownTurn` separates a turn this page
  is streaming from one it merely found running.
- The Harness inspector has four tabs; the files tab downloads through `fetch` + a blob, not a
  bare `<a href>`, because the API needs auth and `apiFetch` returns JSON only.
- **The three floating widgets are draggable and resizable** via `js/widgets/floating-panel.js`,
  which must load *before* `sprite-chat.js` and `music-player.js`. It is generic: a widget says
  what resizing means to it through `onResize`. Two rules it enforces that are easy to undo by
  accident — a drag only starts after 4px of travel (so the play button still takes clicks), and
  it positions with `left`/`top`, never `transform`, because the sprite's idle bob and the clock's
  tick animation already own `transform`. Layouts persist per widget in `localStorage` and are
  clamped into the viewport on load and on window resize.
- **`.textarea` in `base.css` transitions `all`, and `all` includes `height`.** On a box the user
  can drag (`resize: vertical`), that is fatal: the browser writes a new height on every pointer
  move and each one eases over 300ms, so the box trails the cursor and reads as a dead handle.
  `.hs-input` overrides it back to colour-only. Any other resizable element needs the same.
- **One number, one owner.** The Harness composer's height is the textarea's own inline height,
  and its grid row is a `content` track that follows it — so the native corner and the divider
  above it both write the same place. Binding two owners together instead (a fixed track plus a
  `ResizeObserver` pushing back) did not converge: a 260px drag settled at 67px and dragging the
  divider *down* grew the pane. Note a grid `auto`/`content` row is compressed when the container
  runs out of room, so `offsetHeight` is the height you *got*, never the height you asked for —
  a drag must carry its own requested value or it drifts.
- **The music player and clock are sized in `em`** off a single `font-size` on their root, so
  scaling them is one number. Don't reintroduce `px` inside them. The scale is a *ratio* against
  the widget's width at 1rem, so that base must be measured with the widget's stylesheet already
  applied — `ui.js` injects `sprite-chat.css` at runtime for pages whose head lacks it, and a base
  measured before the `<link>` loads is the width of the whole page. With a saved layout,
  180/1400 set the clock to 0.129rem: a 180x11px box holding 2px text. Every page now links the
  widget stylesheets itself (the injection stays as a net), and `clockBaseWidth()` refuses a
  measurement taken while `position` is not yet `fixed`.
- **The sprite is 天启, and it has no text bubble.** It expresses itself through eye shape, eye
  colour and aura (`MOODS` in `sprite-chat.js`). The old `.sprite-greeting` kaomoji bubble was
  removed because its show/hide timers raced with the hover state. Its gaze follows the pointer
  from the sprite's *live* rect, so it keeps working after the sprite is dragged.
- **`new THREE.WebGLRenderer()` throws when a context cannot be created** — not just when THREE is
  missing. Both cases fall back to `useFallbackSprite()`; an uncaught throw there takes the click
  handler and `toggleSpriteChat` down with it and the assistant becomes unreachable.

## The Harness subsystem (`backend/harness/`)

An agent workbench modelled on the ideas of deepseek-ai/deepseek-harness (not a port). Two
invariants hold the design together:

1. **Replaceable seams.** `SessionStore`, `ModelAdapter`, `Sandbox`, `ToolRegistry` are each a
   protocol plus a default implementation, all selected in `harness/context.py`. Nothing else knows
   which implementation it got.
2. **Everything the model sees is logged first.** `harness/session/projection.py::derive_messages()`
   is the *only* function permitted to turn the log into a request. The loop writes each event to
   the store **before** yielding it. That includes the system prompt: it is snapshotted into a
   `config/change` event whenever it differs from the last one logged, because it embeds the
   current date and is rebuilt from `system.md` — recomputing it at read time would replay an old
   session with a date the model never saw. `derive_messages` prefers the logged snapshot and
   falls back to its argument only for sessions recorded before snapshots existed. Keep both
   properties when changing the loop.

`loop/agent.py` produces `SessionEvent`s and knows nothing about HTTP; SSE framing is the router's
job. Only `user/message`, `assistant/message` and `tool/result` project into model messages —
everything else is log-only.

**Data/code separation** is concrete here: a tool's model-facing contract (name, description, JSON
Schema, permission) lives in `harness/data/tools/*.json`; Python supplies only the handler, exported
via a `HANDLERS` dict and bound by name at load time. A contract with no matching handler raises
the first time a `ToolRegistry` is built, rather than mid-conversation.
Prompts, presets, the price table and the shell allowlist are likewise data files.

**Adding a tool is two files**, and no registration code:

1. `harness/data/tools/<module>.json` — the contract. The `name` must match a key in step 2.
2. `harness/tools/builtin/<module>.py` — the handler, exported in a `HANDLERS` dict. Take paths
   through `ctx.workspace.resolve()` and sandbox containment applies for free.

`load_specs()` discovers modules by scanning the *contract* directory, so a `.json` with no
matching `.py` raises at load time while a `.py` with no `.json` is dead code (logged at startup,
never loaded). The scan is `sorted()` on purpose: that order becomes the request's `tools` array,
and the provider's prefix cache only pays while the prefix is byte-identical. Presets accept `"*"`
and `"<module>:*"`; `standard` uses `["*"]`, so a dropped-in tool is live after a restart, while
`minimal` keeps an explicit list because it exists to be a reproducible baseline.

**What takes effect without a restart:** prompt files (`data/prompts/*.md`), tool `description`
text, skills (both `data/skills/*.md` and `data/skills/<name>/SKILL.md` packages) and agent
definitions (`data/agents/*`) — none of those are cached. **What needs a restart:** anything behind an `@lru_cache` — `load_specs()`,
`load_preset()`, the shell allowlist and the price table. So editing a prompt or a skill is live;
adding a tool, preset or allowlist entry is not.

**Skills** come in two shapes: a lone `data/skills/<name>.md`, or a `data/skills/<name>/SKILL.md`
package that carries `scripts/` and `references/` beside it — the layout published skill bundles
use. A package's files are **copied into the session workspace under `.skills/<name>/`** when
`load_skill` runs, because the sandbox can only reach workspace paths; moving the files in is far
safer than widening the containment rule everything else depends on. Frontmatter is two keys
(`description`, optional `keywords`), the filename or directory name is the name, and the body is
reached only through the `load_skill` tool. That routing is
the point: the body lands in the log as a `tool/result`, so it stays inside "model-visible means
logged". Keyword auto-injection was considered and rejected for exactly that reason. A malformed
skill is skipped and reported via `GET /harness/skills`, not raised — unlike a tool contract,
which is a repository packaging error and must fail loudly. The two shipped packages, `docx` and
`pptx`, are the working examples of the packaged shape; see Security for their licence and
read-back constraints.

**Subagents** run a nested `run_turn` under their own session id, sharing the parent's workspace
and sandbox. Three things are easy to get wrong here:

- `manager.usage_summary()` **must** keep rolling up children. A delegating turn routinely spends
  more inside the child than outside it.
- The child runs `StrictApprovalPolicy` (`ask` → `deny`). An approval card raised in a child could
  never be answered: the parent's turn is blocked on the tool call that created it.
- `build_subagent_context()` takes the parent's workspace and sandbox as arguments rather than the
  parent context, so a handler never gets a route back into the whole parent runtime.

Non-obvious behaviour learned the hard way, all commented at the relevant code:

- **Streaming cleanup must not live in the response generator's `finally`.** A client that closes
  its tab leaves the generator suspended and Python may run that `finally` much later or never.
  Cleanup is a Starlette `BackgroundTask`, and `manager.reconcile_status()` heals the row from the
  log as a backstop.
- **Status is derived per turn**, not from the single last event — the loop emits `step/end` *after*
  `tool/approval`. `lifespan` also calls `reset_running_sessions()`, because no turn can outlive the
  process that drove it, so anything still marked `running` at boot was cut off by the last
  shutdown.
- **Compaction triggers on the provider's real `prompt_tokens`** (from `llm/usage`), not a local
  estimate: every request also carries the system prompt and all tool schemas (~1500–2000 tokens),
  which a text-only estimate misses entirely. It refuses to summarise when the gain would be
  trivial, so a too-low budget warns instead of looping.
- **Thinking models (`deepseek-v4-pro`) spend the output budget on reasoning before emitting text.**
  Short-output calls need generous `max_tokens`; `complete()` raises rather than returning an empty
  string when the cap is hit, because a silent `""` hid a broken feature.
- **A model's tool arguments are text, and `write` is where that bites.** File content is mostly
  newlines and models routinely emit real ones inside the JSON string instead of escaping them;
  `parse_arguments` retries with `strict=False` so a `write` is not failed over a quoting detail
  the model cannot see. Truncation is the other failure and is *not* recoverable — the output cap
  ended the call mid-arguments so the string never closes. It must be named as truncation, or the
  model rewrites the same too-long file and is cut off again. `HARNESS_MAX_TOKENS` is that cap
  (4096 truncates any real file; the default is 8192). The two are told apart by the decode error:
  an unterminated string, or a position at end-of-input, means the arguments stopped arriving.
- **Cost is keyed by the exact model string the provider echoes back**, not the family name, and
  a miss reports no cost at all — silently, forever. This deployment ran on `deepseek-flash`
  while `harness/data/pricing.json` listed only `deepseek-v4-flash`, so every session showed a
  dash. `harness_check.py` now fails when the configured model has no rate, `summarize()` returns
  `unpriced_models`, and the UI names them in a tooltip. Adding a model is one line of data.
- **Streamed chunks are batched** (`CHUNK_FLUSH_SIZE` / `CHUNK_FLUSH_SECONDS`) — one commit per
  token cost roughly a fifth of a long turn's wall clock.
- The system prompt carries the current **date only** — second precision would invalidate the
  provider's prefix cache on every request. Exact time comes from the `current_time` tool
  instead, which reads the clock locally. Without it the model either refuses or tries to fetch
  the time from a public API and fails.

### What is *not* pluggable

Tools, presets, skills and agents are all drop-in. What is not:

- **No third-party loading.** `importlib` is used, but only over `harness/tools/builtin/`, a
  package inside this repository. There is no entry-point discovery, no external plugin path, and
  nothing is loaded from anywhere a non-operator can write. Keep it that way.
- **No hot-reload of code.** Handler modules are imported once; adding a tool needs a restart even
  though adding a skill does not.
- **Hooks are code-level.** `pre_step` and `pre_execute` are real extension points, but a listener
  is registered by editing `build_hooks()` in `harness/context.py`.
- **No cross-process interrupts.** `loop/interrupt.py` is a process-local dict, deliberately.

The upstream project's "everything is a plugin" framing still describes *its* design, not this
one — check before repeating a capability claim from it.

### Security posture — read before loosening anything

The sandbox is a fence, not a jail. It guards against the agent doing something unintended (prompt
injection in fetched content, a misread instruction), not against a malicious operator. The **code**
defaults are the safe ones — `HARNESS_REQUIRE_ADMIN=true`, `HARNESS_SHELL_ENABLED=false` — and a new
deployment should start there. **This deployment's `.env` deliberately sets
`HARNESS_SHELL_ENABLED=true`**, because the document skills run scripts. From that point the
approval prompt carries the whole risk, so do not weaken it to compensate.

- Commands run via `execve` on parsed argv — **never through a shell**. Bare shell operators are
  rejected; a quoted `;` is fine because nothing interprets it.
- `data/shell_allowlist.json` auto-approves only commands that can neither execute arbitrary code
  nor open a socket. `python`, `node`, `git`, `curl`, `find`, `awk` deliberately require approval —
  that is the feature working, not a gap to close.
- All file paths pass `core/paths.py::contained_path()`, shared with public uploads (which
  re-exports it from `services/storage_service.py`, where it used to live). Fix path logic there,
  once. It moved because `harness/sandbox/workspace.py` imported it from `services/`, which
  dragged `fastapi` in behind it and made `harness/__init__.py`'s "no fastapi below this package"
  claim false — and blocked `rl/` from importing the harness at all. **The download routes use the same `Workspace.resolve()` the agent
  does** — one containment implementation, not a second one to keep in sync.
- **Third-party skill packages need a licence check first.** The `anthropics/skills` docx/pptx
  bundles are public but ship a proprietary `LICENSE.txt` that forbids retaining copies outside
  Anthropic's Services, copying, and derivative works — they cannot be installed here. The two
  skills in `data/skills/` are independent implementations over `python-docx`, `python-pptx` and
  `pandoc`, all of which are open source.
- **Both document skills require a read-back step.** A `.docx`/`.pptx` is a ZIP of XML, so `read`
  cannot verify one; `read_docx.py` (pandoc) and `read_pptx.py` exist so the agent can check what
  it actually produced instead of reporting success from the writer's exit code.
- **`python3` is deliberately not auto-approved.** With `HARNESS_SHELL_ENABLED=true` a skill's
  scripts can run, but every invocation stops for a human. The sandbox bounds paths, CPU, memory
  and wall clock; it does **not** bound the network, so a script can reach out from the host. The
  approval prompt is the only control on that. Moving `python3` into `auto_approved` removes it.
- **The sandbox's `python3` is `sys.executable`'s directory, prepended to PATH.** Without that the
  agent runs whatever `/usr/bin/python3` is and cannot import what the deployment installed — a
  failure with no visible cause.
- Delegation is a *spend* surface, not an execution one: depth, per-session count, step and
  wall-clock caps all sit in `settings` and are checked before the child session row is created.
  A subagent gains no permission its parent lacks — it loses one.

## The RL workbench (`rl/`)

A Deep Research Agentic-RL environment built *on* the Harness: task set, verifiers, concurrent
multi-turn rollout, token-level loss masks, GRPO. `rl/README.md` is its front door and carries the
measurements; this section is only what a future session needs in order not to break it.

**Dependency direction is one-way and load-bearing.** `rl/` imports `backend/harness`;
`backend/` must never import `rl/`. The corpus reader and BM25 index live in
`backend/harness/corpus/` because they define what the agent *sees* — the tool handler serves them
at rollout time and the offline pipeline reads them at build time, and one implementation is the
only way those two can be guaranteed to agree. The training machinery wrapped around them is
`rl/`. A dependency the other way would put torch in the web server's import graph.

**The environment is data, and the data is hashed.** A reward means nothing except relative to a
corpus and a preset, so `EnvStamp` (corpus sha256, preset, `max_steps`, model, chat-template
sha256) is written into every trajectory, and `export_verl.verify()` *refuses* a file mixing two
stamps. `corpus verify` re-hashes `docs.jsonl` against its manifest. Splits are frozen JSONL with
their own hashes.

**Three seams are reused rather than reimplemented**, which is what the Harness's Protocols were
for: `MemorySessionStore` (4 methods) replaces SQLite so a rollout needs no DB and no user row;
`EvalAdapter` / `PolicyAdapter` replace `OpenAICompatibleAdapter` (whose temperature is hard-coded
and which has no seed); `rl/env/build.py` constructs `HarnessContext` directly instead of calling
`build_context()`. The loop, the projection and the event vocabulary are untouched.

### Things that will bite you in `rl/`

- **Never call `compose_prompt()` for a rollout.** It appends `runtime_context()`, which carries
  *today's date*. A trajectory collected on Tuesday then re-tokenises differently on Wednesday and
  the loss mask's prefix property breaks. `rl/env/build.py::pinned_system_prompt` exists for this.
- **Compaction must stay off.** `maybe_compact` runs every step and rewrites the *system* message
  when it fires, which also breaks the prefix property — silently. The budget is set unreachably
  high and `assert_no_compaction()` checks the log rather than trusting the budget.
- **`derive_messages` hands over `tool_calls[].arguments` as raw JSON text**, and Qwen's chat
  template pipes it through `| tojson`, which double-encodes a string into a JSON string. vLLM
  normalises server-side, so rollout and a naive local re-tokenisation disagree on every tool call
  with no error. `rl/env/template.py::normalize()` is the fix and a check stage pins it.
- **The prefix property that must hold is per-assistant-turn, not per-message.** Qwen merges
  consecutive `tool` responses into one block, so mid-block prefixes genuinely shift; asserting
  stability at every message boundary is a false alarm (it failed on all six real trajectories
  before this was understood).
- **`<|im_end|>` belongs inside the loss mask.** Trained without it, the policy never learns to stop.
- **An empty model response is "no verdict", not "the model doesn't know."** Thinking models spend
  the whole output budget on reasoning at a measurable rate (~18% at a 4096 cap). Scoring those as
  negative admits leaked questions into the dataset, which is what the leakage filter exists to
  prevent. Size `max_tokens` off the *tail*, not the average, and treat empties as unknown.
- **`Teacher.ask_many` separates `EmptyResponseError` from transport failures.** Only the latter
  counts toward the abort threshold; conflating them killed a healthy 4,834-question sweep at
  question 1,000.
- **`group_advantages` exists twice** — pure Python in `rl/train/pack.py` for the loop (so the
  orchestration is testable without a GPU) and on tensors in `rl/train/grpo.py`. A check stage
  asserts they agree numerically; if you change one, change both.
- **`harness/__init__.py` is an eager facade.** Importing *any* harness submodule runs it and pulls
  in `harness.context` → the DB layer → `core.config`. `rl/train/pack.py` keeps its `Trajectory`
  import under `TYPE_CHECKING` for exactly this reason.
- **`engine.run_many` takes a policy *factory*, not an adapter.** Pass `engine.shared(adapter)`
  to reuse one instance (evaluation) or a factory to get one per episode (training, which needs
  it: the policy adapter accumulates the per-generation records `logp_old` comes from, and one
  shared adapter across concurrent episodes interleaves them unattributably). Do not reintroduce
  duck-typing to tell the two apart — an adapter *class* has a `stream` attribute exactly like an
  instance does, and that mistake silently passed the class itself as the adapter.
- **`logp_old` is matched to assistant spans by prompt prefix, never by order.** Concurrent
  rollouts finish out of order. Partial coverage drops the whole vector so the trainer falls back
  to a frozen pass (ratio exactly 1) rather than pairing one generation's log-probs with
  another's tokens.
- **`load_specs()` is `@lru_cache`d**, so a long-running rollout process will not see an edited
  tool contract.
- **The shift convention lives in `grpo.token_logprobs()`.** `logp_old[b, t]` is the log-prob *of*
  `input_ids[b, t]`. Getting it backwards by one produces a finite loss and a wrong gradient; it
  was gotten wrong three times before being given a name.

## README

`README.md` is the user-facing document and is kept current — update it alongside behaviour changes,
especially the config table and the Harness security section. `rl/README.md` is the same contract
for the RL workbench: its measurement tables are the project's main claim, so a behaviour change
that moves a number belongs there too.
