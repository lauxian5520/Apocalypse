# 部署、验收与恢复

本版本面向小规模受邀用户，生产验收基线为 Linux x86_64、Python 3.12、SQLite、单个后端进程与 Nginx。Web 与 GPU 训练环境分开。公网发布需要域名、有效证书和目标服务器；本地测试通过不等于已经上线。

## 权限与兼容

公开笔记保持公开；私密笔记的正文、评论和图片仅作者与管理员可见。私聊附件仅发送者与接收者可见，管理员不会自动获得私聊权限。旧 URL 保留，但文件必须有数据库归属；遗留孤儿文件不再公开下载。公开转私密立即影响后续请求，但无法撤回用户已经下载的副本。

上传验证真实图片格式与大小；HTML 等主动内容强制下载。所有 API 响应禁止缓存。工作区写入默认需要批准，子代理无法请求批准时会拒绝该动作；如在受信任环境明确需要原来的工作区自动写入策略，可配置 `HARNESS_WRITE_APPROVAL=false`，Shell 在生产仍保持关闭。

## 本地与生产启动

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -c constraints/web-py312.txt
cd backend
../.venv/bin/python main.py
```

生产先在 `.env` 设置至少 32 字符的随机 `JWT_SECRET`、实际 Provider 配置、精确的 HTTPS `ALLOWED_ORIGINS`。不要提交 `.env`。运行路径须位于同一个 `VAR_DIR`，便于完整备份。

将域名证书放到 `deploy/certs/fullchain.pem` 和 `deploy/certs/privkey.pem`；该目录已忽略。证书续期后重新加载 Nginx。目录权限先初始化，再启动生产配置：

```bash
mkdir -p var
sudo chown -R 10001:10001 var
docker compose -f docker-compose.yml -f docker-compose.production.yml up --build -d --wait
docker compose -f docker-compose.yml -f docker-compose.production.yml exec backend python -m cli.users admin admin@example.com --admin
docker compose -f docker-compose.yml -f docker-compose.production.yml exec backend python -m cli.users invited invited@example.com
```

CLI 交互式读取密码，不接受命令行明文密码。生产默认关闭公开注册，已有账号继续使用。不要在公网首次启动时依赖“第一个注册者成为管理员”。生产配置启用 Secure Cookie，拒绝弱配置、Shell 和非管理员 Harness。后端不映射宿主机公网端口。

`/healthz` 判断进程存活；`/readyz` 检查数据库连接与数据目录写入。Provider 或抓取源不可用不会使基础站点失去就绪状态。Nginx 对登录、验证码限流，限制请求体，关闭 SSE 缓冲。应用日志使用请求 ID、路由模板、状态及耗时，不记录请求正文和认证头；Docker 日志轮转限制为 3×10 MB。

`PRICING_FILE` 可指向自定义 JSON，格式同后端内置价目表，模型名称须与 Provider 返回值一致。未知模型显示“未配置价格”，不能冒用相似模型价格；损坏价目表作为配置错误处理。

## 自动与真实验收

```bash
python -m pip install -r tools/requirements-checks.txt -c constraints/web-py312.txt
python -m playwright install --with-deps chromium
python tools/release_check.py --group web
# 以下命令在独立 RL 环境执行：
python -m pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple -c constraints/rl-cpu-py312.txt
python -m pip install -r rl/requirements.txt -c constraints/rl-cpu-py312.txt
python -m rl.cli setup
python -m rl.cli setup --model Qwen/Qwen3-8B
python tools/release_check.py --group rl
```

Web 和 RL CPU 的约束均已在独立全新虚拟环境安装验证。`constraints/rl-gpu-py312.txt` 是现有 GPU 环境的已安装依赖快照，本轮没有重建 GPU 环境或重跑训练；实际 Python、CUDA 构建和关键库版本见 `constraints/environment.json`。准备阶段下载后，离线门槛校验语料哈希与 `constraints/rl-inputs.json` 中 tokenizer/模板指纹，变化或缺失均失败。

检查创建临时数据库、账号和文件，使用本机模拟模型；不得指向正式数据目录。浏览器覆盖桌面与 390px 窄屏。JSON 结果和各阶段日志在 `artifacts/checks/`，浏览器截图在 `artifacts/browser/`。RL CPU 自检唯一允许跳过的是明确关闭的真实 Provider rollout，不能跳过数值损失、模板或本地 LoRA 服务检查。

CI 在 PR/主分支运行 Web、RL 与 Docker/HTTPS 检查；发布标签先执行相同工作流，失败不能创建 Release。仓库管理员还需在分支保护设置中将这些作业设为必需检查。

离线验收通过后，使用部署者已有 Provider 配置执行：

```bash
python tools/live_check.py
```

最多 10 次请求，每次输出最多 1024 token，自动标题关闭，重试也计数。预算记录持久化在 `artifacts/live/budget.json`，脚本拒绝对已有调用记录自动重跑；不得删除预算文件以绕过本轮额度。报告只包含状态、请求次数和观测 Token，不记录 Key 或私密正文。中断时 Provider 可能不返回终态用量，因此观测 Token 不是账单总额。

## 备份、恢复与回滚

备份工具使用标准库。先停止后端；与应用共享的维护锁会拒绝在线备份。旧版本尚未持有维护锁时，必须人工确认进程已停止。一次归档涵盖 SQLite 快照和整个 `var`，证书与 `.env` 独立保管。

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml stop backend
python tools/backup.py create --var-dir var --database db/myweb.db --out /BACKUP_DISK/apocalypse-YYYYMMDD.tar.gz
docker compose -f docker-compose.yml -f docker-compose.production.yml start backend
python tools/backup.py verify /BACKUP_DISK/apocalypse-YYYYMMDD.tar.gz
python tools/backup.py restore /BACKUP_DISK/apocalypse-YYYYMMDD.tar.gz --target var-restored
```

`--database` 是相对 `var` 的实际数据库路径。恢复默认拒绝非空目录，验证文件校验和、SQLite 完整性及外键后再安装目录。归档不可包含链接或路径穿越。恢复后按容器 UID 10001 赋予数据目录所有权；以恢复出的目录启动隔离候选实例，并核对账号、笔记、附件、私聊和 Agent 历史。

每日安排一次维护窗口完成备份，复制到不同磁盘或主机后再执行 `python tools/backup.py prune /BACKUP_DISK`。保留最近 7 个不同日期的最新备份和最近 4 个不同 ISO 周的最新备份，取并集；只管理 `apocalypse-*.tar.gz`。先验证新归档，再清理旧归档。调度由服务器的 cron/systemd 执行，本次不会修改宿主机计划任务。

升级前记录旧镜像 ID，并保留完整归档；回滚同时恢复匹配版本的镜像、数据库与文件，禁止仅回滚代码后假定新 schema 兼容。恢复演练命令：`python tools/backup_check.py`。

## RL 成果与发布记录

参见 [实验报告](experiments/README.md)。`python tools/package_rl.py` 重算已有报告并打包两份 adapter、原始轨迹与必要日志；不会调用模型或训练。完整包保存在 `artifacts/rl-delivery/`，使用随包 sha256 校验。旧实验缺少可核验训练提交，文档已明确记录，不能用交付提交冒充。

每个候选版本保留测试 JSON、部署日志、恢复耗时、真实调用结果及产物校验和。尚无服务器或无法运行 Docker/HTTPS 时，将相应门槛记为“未验收”；不能把功能实现完成写成上线完成。

源代码候选包：审阅并暂存源码后执行 `python tools/package_candidate.py`，输出在 `artifacts/candidate/`。包装清单记录基础提交、暂存区 tree ID 与归档 sha256；不会创建提交，也不包含 `.git`、运行数据或 `.env`。解压后按本手册配置独立运行目录。RL 包解压到同一源码根目录后可用 `python tools/package_rl.py` 从逐题记录重算报告。
