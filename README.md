# 天启 🚀

> AI 驱动的个人知识空间 — 沉浸式前端 + 社交笔记 + Python 全栈后端

## ✨ 功能特性

- **📝 社交笔记** — 发帖（Markdown + 多图）、评论、匿名发布、AI 解释
- **🤖 AI 助手** — 3D 精灵对话（SSE 流式）、页面总结、内容解释（DeepSeek / 智谱 / Gemini / OpenAI / Ollama / 自定义）
- **🧩 Harness 智能体工作台** — 会话式 Agent：工具调用、人工审批、沙箱工作区、全链路事件日志、轨迹检查器、Token 计费、上下文压缩、会话 fork/replay（详见下方专节）
- **⚡ 热门项目** — GitHub Trending + HuggingFace 模型（每日/每周/每月）
- **📄 前沿论文** — HuggingFace Daily Papers + arXiv
- **🔥 焦点新闻** — 多平台热点聚合
- **🛠 工具箱** — Markdown 编辑器、颜色工具、番茄钟、密码生成器等
- **🎵 背景音乐** — 管理员上传/管理，全局悬浮播放器
- **💬 私聊** — 用户间一对一消息，支持图片/文件附件
- **🔐 用户系统** — 图形验证码注册、Cookie 会话、头像上传、后台用户管理
- **🛡 安全** — HMAC 无状态验证码、bcrypt 密码哈希、HttpOnly Cookie + CSRF 双提交校验

## 📂 项目结构

设计原则：**数据与代码分离**、**分层单向依赖**、**同层高内聚低耦合**。

```
Apocalypse/
├── backend/                  # 后端代码（只读，不含任何运行时数据）
│   ├── main.py               # 应用装配：中间件 → 异常处理 → 路由 → 静态文件
│   ├── core/                 # 基础设施：谁都可以依赖，它不依赖任何人
│   │   ├── paths.py          #   路径解析（唯一的目录真相来源）
│   │   ├── config.py         #   .env 配置
│   │   ├── database.py       #   引擎 / 会话工厂 / Base
│   │   ├── security.py       #   密码哈希、JWT、Cookie（纯函数）
│   │   ├── deps.py           #   FastAPI 依赖：get_db / require_user / require_admin
│   │   ├── providers.py      #   LLM provider 解析（chat 与 harness 共用一份）
│   │   ├── sse.py            #   SSE 帧格式（前端手工解析，格式不可改）
│   │   └── errors.py         #   领域异常（服务层抛出，main.py 翻译成 HTTP）
│   ├── models/               # ORM 实体：只描述表与关系
│   │   └── migrations.py     #   建表 + 轻量列补齐（无副作用导入）
│   ├── schemas/              # Pydantic 出入参契约（跨路由共享，不重复定义）
│   ├── services/             # 业务逻辑：可用 models/core，禁止 import FastAPI 请求对象
│   │   ├── ai_service.py     #   LLM 统一客户端（流式 / 非流式）
│   │   ├── storage_service.py#   统一上传落盘与校验
│   │   ├── memo_service.py   #   帖子加载与匿名投影
│   │   ├── captcha_service.py
│   │   ├── feed_service.py   #   抓取调度 + JSON 缓存
│   │   └── classify_service.py
│   ├── routers/              # HTTP 层：解析请求 → 调 service → 返回 schema
│   │   └── __init__.py       #   api_router：URL 映射的唯一定义处
│   ├── scrapers/             # 外部数据采集（纯抓取，不关心存储）
│   └── harness/              # 智能体子系统：与 services 同层，内部不出现 fastapi
│       ├── context.py        #   装配点：所有接缝在这里选定实现
│       ├── events.py         #   事件信封与类型常量
│       ├── loop/             #   turn/step 状态机、钩子总线、中断令牌
│       ├── session/          #   存储接缝、消息投影、压缩、标题、会话管理
│       ├── llm/              #   模型适配器接缝（工具调用流式拼装）+ 计费
│       ├── tools/            #   工具注册表、审批策略、内置工具实现
│       ├── sandbox/          #   工作区路径收敛 + 受限子进程执行
│       └── data/             #   ⭐ 纯数据：提示词、工具契约、预设、价格表、白名单
│
├── frontend/                 # 前端代码（只读）
│   ├── *.html                # 页面骨架，不含内联 JS/CSS
│   ├── css/
│   │   ├── base.css          #   设计变量与通用组件
│   │   ├── components/       #   可复用部件样式
│   │   └── pages/            #   每页专属样式
│   └── js/
│       ├── core/             #   utils → auth → api → ui（按此顺序加载）
│       ├── widgets/          #   sprite-chat、music-player
│       └── pages/            #   每页专属逻辑
│
├── var/                      # ⚠️ 运行时数据（唯一可变目录，整体备份即可）
│   ├── db/                   #   SQLite 数据库
│   ├── uploads/              #   帖子图片、评论图片、聊天附件、头像
│   ├── music/                #   背景音乐
│   ├── feeds/                #   抓取结果 JSON 缓存
│   └── harness/workspaces/   #   每个 Agent 会话独占的沙箱目录
│
├── deploy/                   # 部署产物：Dockerfile、nginx.conf
├── tools/                    # 开发辅助脚本（不参与运行，不进镜像）
├── docker-compose.yml
└── .env.example
```

### 依赖方向

```
routers  →  services | harness  →  models
    └──────────┴─────────┴──────────┴────→  core
```

上层可以依赖下层，反向禁止。`core` 不 import 任何业务模块；`services` 与 `harness` 不 import
`routers`，也不接触 `Request`/`HTTPException`，出错时抛 `core.errors` 里的领域异常，由
`main.py` 统一翻译成状态码。`harness/` 与 `services/` 同层，是一个自成一包的子系统。

## 🧩 Harness 智能体工作台

一个部署在自己服务器上的 Agent 工作台，参照 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
的两个核心思想重写（不是移植它的 TypeScript 代码）：

1. **可替换的能力接缝** — 模型适配器、工具注册表、会话存储、沙箱各自是「一个协议 + 一个默认实现」，
   在 `backend/harness/context.py` 里选定。换实现改一行，调用方无感。
2. **全链路可追溯** — 送给模型的一切都先写进 append-only 事件日志，
   `harness/session/projection.py` 的 `derive_messages()` 是**唯一**能把日志变成请求的函数。
   页面右栏的「模型可见消息」页签直接展示它的输出，这个说法是可以当场核对的。

访问 `/harness.html`。

### 一轮对话发生了什么

```
turn/start
  step/start → assistant/chunk… → assistant/message → llm/usage
            → tool/call → [审批] → tool/result
  step/end  → （还有工具要调就再来一步）
turn/end
```

事件类型采用与上游一致的斜杠命名。其中只有 `user/message`、`assistant/message`、`tool/result`
会投影成模型消息，其余（`assistant/chunk`、`step/*`、`llm/usage`…）仅用于日志、重放与轨迹视图。

### 内置工具

| 工具 | 权限 | 说明 |
|---|---|---|
| `read` / `write` / `edit` / `glob` / `grep` | read / write | 文件操作，路径强制收敛在会话工作区内 |
| `bash` | exec | 单条命令，无管道/重定向；默认关闭 |
| `web_fetch` / `web_search` | read | 抓取网页正文 / DuckDuckGo 检索 |
| `current_time` | read | 服务器当前精确时间，支持指定 IANA 时区，不联网 |
| `todo_write` / `exit_plan_mode` | read | 任务清单与方案确认 |

工具的**模型可见契约**（名称、描述、JSON Schema、权限位）住在 `harness/data/tools/*.json`，
Python 里只有 handler，注册时按名字绑定。改一句工具描述不需要动代码。
同理，提示词在 `data/prompts/*.md`，运行模式在 `data/presets/*.json`，价格表在 `data/pricing.json`，
shell 白名单在 `data/shell_allowlist.json`。

### 🛡 安全模型（部署前请读完）

沙箱是**纵深防御的围栏，不是牢笼**。它挡的是「Agent 干出意料之外的事」——被抓取的网页里藏了提示注入、
指令被误读——而不是恶意的管理员（那个人本来就能直接批准任何操作）。拿到任意命令执行的攻击者
理论上仍可能逃逸。因此实际保护来自叠加的几层：

| 层 | 措施 |
|---|---|
| 谁能用 | `HARNESS_REQUIRE_ADMIN=true`（默认）——只有管理员。项目里第一个注册的账号即管理员 |
| 能不能执行命令 | `HARNESS_SHELL_ENABLED=false`（默认）——不打开就没有 `bash` 工具 |
| 命令边界 | argv 解析后执行，**从不经过 shell**；`data/shell_allowlist.json` 里的只读命令自动放行，`sudo`/`ssh`/`docker` 等直接拒绝，**其余一律转人工审批**——包括 `python`、`node`、`git`、`curl`、`find`、`awk`，因为它们能执行任意代码或联网 |
| 文件边界 | 每个会话独占 `var/harness/workspaces/{id}/`，所有路径过 `contained_path()`（与图片上传共用同一份检查），`..`、绝对路径、指向外部的软链接全部拒绝 |
| 资源边界 | CPU / 地址空间 / 文件大小 `setrlimit`，墙钟超时后杀整个进程组，输出截断，工作区容量配额 |
| 容器边界 | `cap_drop: [ALL]`、`no-new-privileges`、`pids_limit`、以非 root 用户（uid 10001）运行 |

> `RLIMIT_NPROC` 是**故意没设**的：它按真实 UID 统计，设低了会先饿死 Web 服务自己的 worker。
> fork 炸弹由超时 + 进程组 kill + `pids_limit` 兜底。

用 Docker 时宿主机的 `./var` 需要能被容器内的 uid 10001 写入：

```bash
sudo chown -R 10001:10001 ./var
```

### 会话与成本

- **状态自愈** — 浏览器中途关掉，流式响应的生成器不会立刻被回收，所以收尾工作放在 Starlette 的
  background task 里（断连也会执行）；`manager.reconcile_status()` 再以事件日志为准兜底，
  会话不会卡在 `running`。
- **上下文压缩** — 触发条件用的是 provider 回报的**真实** `prompt_tokens`（`llm/usage` 事件），
  不是本地估算：一次请求除了对话，还要带系统提示词和全部工具 schema，这两项就有一两千 token，
  纯靠估算会严重低估而迟迟不压缩。压缩点只会落在 `user/message` 之前，避免把工具调用和它的
  结果切开。**原始事件一条都不删**，只是不再投影。
  注意 `HARNESS_CONTEXT_BUDGET_TOKENS` 必须明显高于那个固定开销（standard 模式约 1500–2000
  token），否则压缩再多也降不下来；这种情况下系统会拒绝压缩并打一条可操作的警告，而不是
  每一步都白花一次摘要请求。
- **时间** — 系统提示词只带**日期**（秒级时间戳会让 provider 的前缀缓存每次失效，实测缓存
  能省下约 8000/9900 的输入 token）。需要精确时刻由 `current_time` 工具提供，本地读取不联网。
  注意它读的是**服务器时区**——Docker 容器默认 UTC，需要本地时区就在 `docker-compose.yml`
  的 backend 服务加 `environment: TZ: Asia/Shanghai`。
- **思考型模型** — `deepseek-v4-pro` 这类模型会先花输出预算做推理，再吐正文。所以标题、
  摘要这类短输出的 `max_tokens` 必须留足余量（代码里已按 512 / 3000 设置）；预算给小了会
  拿到空正文，此时适配器直接报错而不是返回空串——这个坑最初就是靠这条报错才发现的。
- **计费** — `data/pricing.json` 是数据文件；表里查不到的模型显示「—」而不是 0，
  token 数无论如何都是准确的。价格会变，请对照 [DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing) 自行核对。
- **提示词快照** — 系统提示词会随 `config/change` 事件写进日志（仅在内容变化时写，不刷日志）。
  因为它包含当天日期、且由 `system.md` 拼出来，读取时重算会让历史会话显示模型当时没见过的
  日期，改过 `system.md` 后更会把全部历史一起改写。投影优先用日志里的快照，没有快照的老会话
  才回落到当前配置。顺带的好处是能看出每个会话当时跑的是哪一版提示词。
- **fork / replay** — 从任意事件序号分支出新会话，历史与工作区一并复制。

### 验证

**连通性自检** —— 逐段检查每一环，某一段失败不影响后续，一次跑完就知道断在哪：

```bash
cd backend && python ../tools/harness_check.py --offline      # 只查本地接线，不花 token
cd backend && python ../tools/harness_check.py                # 加上真实 provider 调用
cd backend && python ../tools/harness_check.py --url http://localhost:8000 --token <管理员 JWT>
```

检查项：配置 → 数据库 → 沙箱与路径收敛 → 工具注册表 → 审批策略 → 事件日志与消息投影 →
模型调用 → 工具调用（流式拼装）→ 完整一轮 → HTTP 接口与 SSE。全部通过时退出码 0，
有失败时退出码 1，可直接接进部署脚本。

```
本地接线
  ✓ 配置                        deepseek/deepseek-chat · key 已配置
  ✓ 数据库                      sqlite · harness_sessions / harness_events 就绪
  ✓ 沙箱与路径收敛              读写正常 · 越界与绝对路径均被拒绝 · 配额 64MB
  ✓ 工具注册表                  契约绑定 10 个 · 本模式启用 10 个 · shell 开
  ✓ 审批策略                    放行 / 询问 / 拒绝 三类判定均正确
  ✓ 事件日志与消息投影          3 条事件 → 4 条消息 ['system', 'user', 'assistant', 'tool']
```

**跑一整轮** —— 不经浏览器执行一次真实任务，打印事件日志、投影出的消息、用量与工作区文件：

```bash
cd backend && python ../tools/harness_probe.py --prompt "在工作区建一个 hello.txt 写入当前时间，然后读回来"
```

加 `--keep` 保留会话与工作区以便检查。

## 🚀 快速启动

### 方式一：本地开发

```bash
cp .env.example .env          # 编辑 .env：填 AI API Key，改 JWT_SECRET
cd backend
pip install -r requirements.txt
python main.py
```

浏览器访问 `http://localhost:8000`（后端同时托管前端静态文件）。

> 运行目录无关：所有路径都相对项目根解析，从任何目录启动都用同一份 `var/` 数据。

### 方式二：Docker Compose（推荐生产部署）

```bash
cp .env.example .env
docker compose up -d
```

访问 `http://your-server-ip`。运行时数据通过 `./var` 单个卷挂载。

## ⚙️ 配置说明 (`.env`)

| 变量 | 说明 | 示例 |
|------|------|------|
| `VAR_DIR` | 运行时数据根目录，一个开关搬走全部状态 | `./var` |
| `DB_TYPE` | 数据库类型 | `sqlite \| mysql \| postgresql` |
| `AI_PROVIDER` | AI 提供商 | `deepseek \| gemini \| zhipu \| openai \| ollama \| custom` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | `sk-xxx` |
| `JWT_SECRET` | 签名密钥（**务必修改**，同时用于登录与验证码） | 随机字符串 |
| `COOKIE_SECURE` | 是否仅 HTTPS 发送 Cookie | `false`（开发）/ `true`（生产） |
| `ALLOWED_ORIGINS` | CORS 白名单 | `http://localhost:8000` |
| `DEV_RELOAD` | 热重载，**仅本地开发** | `false` |
| `HARNESS_REQUIRE_ADMIN` | Harness 仅管理员可用，公网部署保持 `true` | `true` |
| `HARNESS_SHELL_ENABLED` | 是否给 Agent `bash` 工具（读完安全模型再开） | `false` |
| `HARNESS_PRESET` | 运行模式 | `standard \| minimal` |
| `HARNESS_MODEL` | Harness 专用模型，留空跟随 provider | 空 |
| `HARNESS_WORKSPACE_QUOTA_MB` | 每个会话工作区容量上限 | `64` |

### 接入其他模型

```env
AI_PROVIDER=zhipu
ZHIPU_API_KEY=你的 API Key
ZHIPU_MODEL=glm-4.7-flash
```

改完重启后端生效。用 `python ../tools/ai_probe.py` 可单独验证 Key 是否可用。

### 认证机制

- 登录后服务端下发 `HttpOnly` 认证 Cookie，前端不保存 JWT。
- 所有写操作（POST/PATCH/PUT/DELETE）自动携带 `X-CSRF-Token`，后端做双提交校验。
- 生产环境务必设置 `COOKIE_SECURE=true` 并启用 HTTPS。

## 👤 首次使用

1. 访问 `/register.html` 注册（**第一个账号自动成为管理员**）
2. 登录后即可发帖、评论、私聊
3. 管理员访问 `/admin.html` 管理用户与背景音乐
4. `.env` 填好 AI Key 并重启，即可使用 AI 功能

## 🔑 API 文档

启动后访问 `http://localhost:8000/docs` 查看 Swagger 交互式文档，
`http://localhost:8000/healthz` 为健康检查端点。

## 📄 License

MIT
