# 天启 🚀

> AI 驱动的个人知识空间 — 沉浸式前端 + 社交笔记 + Python 全栈后端

## ✨ 功能特性

- **📝 社交笔记** — 发帖（Markdown + 多图）、评论、匿名发布、AI 解释
- **🤖 AI 助手** — 3D 精灵对话（SSE 流式）、页面总结、内容解释（DeepSeek / 智谱 / Gemini / OpenAI / Ollama / 自定义）
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
│   └── scrapers/             # 外部数据采集（纯抓取，不关心存储）
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
│   └── feeds/                #   抓取结果 JSON 缓存
│
├── deploy/                   # 部署产物：Dockerfile、nginx.conf
├── tools/                    # 开发辅助脚本（不参与运行，不进镜像）
├── docker-compose.yml
└── .env.example
```

### 依赖方向

```
routers  →  services  →  models
    └──────────┴──────────┴────→  core
```

上层可以依赖下层，反向禁止。`core` 不 import 任何业务模块；`services` 不 import `routers`，
也不接触 `Request`/`HTTPException`，出错时抛 `core.errors` 里的领域异常，由 `main.py`
统一翻译成状态码。

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
