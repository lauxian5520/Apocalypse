<div align="center">

<img src="docs/assets/apocalypse-hero.webp" alt="Apocalypse — Build your knowledge coordinate system" width="100%" />

# Apocalypse

**A self-hosted knowledge workspace that connects notes, papers, developer trends, news, and an inspectable AI agent.**

[简体中文](README.md) · [Quick start](#quick-start) · [Features](#feature-map) · [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/lauxian5520/Apocalypse/issues)

[![License: MIT](https://img.shields.io/badge/License-MIT-7c3aed.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Release](https://img.shields.io/github/v/release/lauxian5520/Apocalypse?include_prereleases&sort=semver)](https://github.com/lauxian5520/Apocalypse/releases)
[![Stars](https://img.shields.io/github/stars/lauxian5520/Apocalypse?style=social)](https://github.com/lauxian5520/Apocalypse/stargazers)

</div>

## Why Apocalypse?

- **More than another chat box.** Notes, papers, GitHub Trending, Hugging Face models, and news live in one workflow where AI can explain and summarize page context.
- **An agent you can inspect.** Review model-visible messages, tool calls, approvals, token usage, append-only events, and complete replayable trajectories.
- **Self-hosted by design.** Run the FastAPI backend, native frontend, and SQLite storage with Docker Compose. All mutable state is contained in `var/` for simple backups.
- **Provider-neutral.** Use DeepSeek, Zhipu, Gemini, OpenAI, Ollama, or any compatible endpoint.

If Apocalypse is useful to you, consider giving it a ⭐. It helps other developers looking for a self-hosted knowledge workspace discover the project.

## Feature map

| Knowledge inputs | AI and execution | Capture and collaboration |
|---|---|---|
| GitHub Trending, Hugging Face, arXiv, multi-source news | Streaming chat, page summaries, explanations, tool calling | Markdown notes, multi-image posts, comments, direct messages |
| Daily, weekly, and monthly feeds | Human approval, sandboxing, trajectory inspection, context compression | Local database, unified backups, admin console |

### Agent workbench highlights

- Replaceable model, tool registry, session store, and sandbox interfaces.
- Append-only event log as the source of truth for model-message projection.
- Fork and replay from any event; raw events are never deleted during context compression.
- Shell disabled by default, admin-only access, path containment, resource limits, and manual approval for unsafe commands.
- Built-in offline wiring checks and live provider probes.

## Quick start

### Docker Compose (recommended)

```bash
git clone https://github.com/lauxian5520/Apocalypse.git
cd Apocalypse
cp .env.example .env
docker compose up -d
```

Open <http://localhost>. The first registered account becomes the administrator. Core knowledge-space features work without an AI key; configure a cloud provider or local Ollama in `.env` to enable AI features.

### Local development

```bash
cp .env.example .env
cd backend
pip install -r requirements.txt
python main.py
```

Then open <http://localhost:8000>. Apocalypse requires Python 3.10 or later.

## Architecture

```text
routers  ->  services | harness  ->  models
    \-------------- all layers ------------>  core
```

- `backend/core`: configuration, storage paths, security, database, providers, SSE, and domain errors.
- `backend/services`: application logic independent from FastAPI request objects.
- `backend/harness`: event-sourced agent loop, tools, approvals, sandbox, adapters, and session projection.
- `frontend`: dependency-light HTML, CSS, and JavaScript UI.
- `var`: the only mutable runtime directory; database, uploads, feeds, music, and agent workspaces.

For the complete architecture, security model, tool contracts, and verification commands, see the [Chinese technical documentation](README.md#-项目结构).

## Configuration

Copy `.env.example` to `.env`. The most important settings are:

| Variable | Purpose |
|---|---|
| `AI_PROVIDER` | `deepseek`, `gemini`, `zhipu`, `openai`, `ollama`, or `custom` |
| `JWT_SECRET` | Authentication and captcha signing secret; change before deployment |
| `COOKIE_SECURE` | Enable after HTTPS is configured |
| `HARNESS_REQUIRE_ADMIN` | Keep the agent workbench admin-only on public deployments |
| `HARNESS_SHELL_ENABLED` | Disabled by default; read the security model before enabling |
| `VAR_DIR` | Root directory for all mutable runtime state |

## Contributing and security

Bug reports, feature proposals, documentation improvements, and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR. Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

MIT
