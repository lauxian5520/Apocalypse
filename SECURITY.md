# Security Policy

## Supported version

安全修复优先应用到 `main` 分支的最新版本。项目尚未发布稳定版，旧提交不保证获得回溯修复。

## Reporting a vulnerability

请不要为未修复的漏洞创建公开 Issue，也不要在讨论区发布可利用细节。

优先使用仓库的 **Security → Report a vulnerability** 私密报告入口。如果该入口不可用，请通过 GitHub 个人主页联系维护者，并只发送必要的概述，等待安全的后续沟通渠道。

报告建议包括：

- 受影响的提交或版本；
- 可复现步骤与最小验证样例；
- 实际影响与所需前置条件；
- 你建议的修复或缓解方案（如有）。

## Deployment baseline

- 公网部署必须更换 `JWT_SECRET`，启用 HTTPS，并设置 `COOKIE_SECURE=true`。
- 保持 `HARNESS_REQUIRE_ADMIN=true` 与 `HARNESS_SHELL_ENABLED=false`，除非已经理解并接受风险。
- Agent 沙箱是纵深防御，不是强隔离边界；不要把恶意管理员或任意代码执行者视为受控对象。
- 不要提交 `.env`、API Key、数据库、上传内容、日志或 `var/` 目录。
