# Apocalypse Promotion Kit

用于项目发布时保持描述一致。所有文案只陈述仓库中已经存在的能力，不填写虚构指标。

## 一句话介绍

天启 Apocalypse 是一个可自托管的 AI 个人知识工作台，把笔记、论文、GitHub/Hugging Face 热点、多源新闻与可追溯 Agent 收进同一条工作流。

## 50 字版本

我做了一个可自托管的 AI 知识空间：聚合笔记、论文、开源趋势和热点；内置 Agent 工具调用、人工审批、沙箱、事件日志、Token 计费与 fork/replay。FastAPI + 原生前端，Docker Compose 可启动。

## English launch copy

Apocalypse is a self-hosted knowledge workspace that connects notes, papers, GitHub/Hugging Face trends, news, and an inspectable AI agent. The agent includes tool calling, human approval, sandboxed workspaces, append-only events, token accounting, context compression, and fork/replay. Built with FastAPI and a dependency-light native frontend.

## Hacker News

**Title**

```text
Show HN: Apocalypse – A self-hosted knowledge workspace with an inspectable AI agent
```

**Opening comment**

```text
I built Apocalypse because my notes, papers, developer feeds, and AI conversations lived in separate tools. It combines them in one self-hosted workspace.

The part I care most about is inspectability: model-visible messages are derived from an append-only event log, and tool calls, approvals, usage, context compression, and fork/replay can all be inspected. The shell tool is disabled by default and workspaces are path-contained.

It runs on FastAPI + a native frontend and supports DeepSeek, Zhipu, Gemini, OpenAI, Ollama, and compatible endpoints. Feedback on the agent architecture and first-run experience would be especially useful.
```

## Reddit / developer forum

```text
I open-sourced Apocalypse, a self-hosted AI knowledge workspace.

It combines Markdown notes, papers, GitHub/Hugging Face trends, multi-source news, and an AI assistant. The built-in agent workbench exposes tool calls, approvals, token usage, model-visible messages, and replayable event trajectories instead of hiding them behind a chat UI.

Stack: FastAPI, SQLite, native HTML/CSS/JS, Docker Compose. Providers: DeepSeek, Zhipu, Gemini, OpenAI, Ollama, and custom compatible endpoints.

Repository: https://github.com/lauxian5520/Apocalypse

I would value concrete feedback on deployment friction, security boundaries, and which knowledge workflow should be improved first.
```

## 中文社区

```text
开源了「天启 Apocalypse」：一个可自托管的 AI 个人知识工作台。

它把 Markdown 笔记、论文、GitHub/Hugging Face 趋势、多平台热点和 AI 助手放进同一条工作流。Agent 工作台可以查看工具调用、人工审批、Token、模型可见消息和完整事件轨迹，支持上下文压缩、fork/replay；默认关闭 Shell，并提供路径收敛和资源限制。

技术栈是 FastAPI + SQLite + 原生前端，Docker Compose 可启动，支持 DeepSeek、智谱、Gemini、OpenAI、Ollama 与兼容端点。

项目地址：https://github.com/lauxian5520/Apocalypse

欢迎试用，尤其希望收到关于首次部署体验、Agent 安全边界和知识工作流的具体建议。
```

## 推荐 Topics

```text
self-hosted, knowledge-base, ai-agent, fastapi, deepseek, ollama, llm, personal-knowledge-management, agent-workbench, event-sourcing, docker-compose
```

## 发布前清单

- README 首屏图片与命令在 GitHub 上正常显示。
- 创建一个带截图和完整说明的首个 Release。
- 设置仓库 Topics、Website（若已有在线演示）和 Social preview。
- 发布后 24 小时内集中回复高质量问题，不做批量私信或互赞交换。
- 所有渠道使用相同的一句话定位，但根据社区补充不同技术细节。
