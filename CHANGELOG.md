# Changelog

Apocalypse 的重要变化记录在此文件。版本号遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Added

- **深研场（`rl/`）**：建在 Harness 之上的 Deep Research Agentic RL 环境——HotpotQA 冻结语料与手写
  BM25、泄漏过滤（双模型并集）与冻结切分、精确验证器与反作弊、并发多轮 rollout、钉死的 chat
  template 与 token 级 loss mask、SFT 冷启动、GRPO、vLLM LoRA 热插拔，以及 `rl_check` 分段自检。
  `corpus_*` 工具由 `HARNESS_CORPUS_ENABLED` 门控，默认关闭，线上工具集不变。
- Harness 技能包（带脚本的 `SKILL.md` 目录，含 docx / pptx）与子代理（`researcher` / `coder`）。
- 工作台可拖拽分栏（双击复位、按浏览器记住）、对话区与输入框分隔条、可缩放的工具输出框与「宽屏」模式。
- `HARNESS_MAX_TOKENS`：单次模型调用的输出上限，默认 8192（原先硬编码 4096）。
- `harness_check.py` 新增「工具参数解析」「费用价目表」两个阶段。

### Fixed

- Harness 提问时页面卡死：检查器在流式过程中改为只追加新事件，不再逐个 chunk 重建整张表。
- 刷新后无法中断仍在运行的会话：页面按服务端状态接回「运行中」并露出「中断」。
- `write` 报「参数不是合法 JSON」：裸换行 / 制表符可无损恢复；输出触顶导致的截断被如实报为截断。
- Harness 预估费用恒为「—」：价目表补上 `deepseek-flash`，未定价的模型会被点名。
- 热门项目页「AI 总结」按钮无响应。
- 后台、登录、注册页的时钟被缩成 2px 文字。
- 工作台在短视口下对话区只剩一行、页面多出滚动条，以及输入框右下角拖不动。

## [1.0.0] - 2026-09-10

首个公开版本：一个可自托管的 AI 个人知识空间与可追溯 Agent 工作台。

### Included

- Markdown 多图笔记、评论、私聊、附件与用户管理。
- GitHub Trending、Hugging Face、arXiv 与多平台热点聚合。
- DeepSeek、智谱、Gemini、OpenAI、Ollama 与兼容端点。
- SSE 流式 AI 助手与页面上下文总结/解释。
- Agent 工具调用、人工审批、沙箱、事件日志、Token 计费、上下文压缩与 fork/replay。
- FastAPI + 原生前端 + SQLite，支持 Docker Compose 部署。
- 双语项目主页、贡献指南、安全策略、Roadmap 和结构化 Issue/PR 模板。

[Unreleased]: https://github.com/lauxian5520/Apocalypse/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/lauxian5520/Apocalypse/releases/tag/v1.0.0
