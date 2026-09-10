# Contributing to Apocalypse

感谢你愿意参与 Apocalypse。清晰、聚焦、可验证的贡献最容易被合并。

## 开始之前

1. 搜索现有 [Issues](https://github.com/lauxian5520/Apocalypse/issues)，避免重复工作。
2. Bug 修复可以直接提交 PR；较大的功能请先开 Feature Request 说明使用场景与边界。
3. 安全漏洞不要公开提交 Issue，请按 [SECURITY.md](SECURITY.md) 处理。

## 本地运行

```bash
cp .env.example .env
cd backend
pip install -r requirements.txt
python main.py
```

访问 <http://localhost:8000>。项目要求 Python 3.10+。

## 代码约定

- 保持 `routers -> services | harness -> models -> core` 的单向依赖。
- Python 代码使用类型注解、Docstring 和 `snake_case`；类名使用 `PascalCase`。
- 运行时数据只写入 `var/`，不要把数据库、密钥、上传文件或缓存提交到仓库。
- 前端页面不写内联 JavaScript/CSS；通用逻辑放在 `frontend/js/core` 或组件目录。
- 新增配置项时同步更新 `.env.example` 与 README。

## 提交前检查

```bash
python -m compileall backend tools
cd backend
python ../tools/harness_check.py --offline
```

`harness_check.py --offline` 需要本地数据库中已有一个用户，才能完整检查消息投影阶段。若只改文档，请确认 Markdown 链接与命令仍然有效。

## Pull Request

- 一个 PR 只解决一个问题。
- 描述动机、改动、验证方式和潜在风险。
- UI 变更请附前后截图；行为变更请补充可复现步骤。
- 不要提交 `.env`、API Key、数据库、日志或 `var/` 中的内容。
