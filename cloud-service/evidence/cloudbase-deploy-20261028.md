# CloudBase schema v16 函数部署与基础验收（2026-10-28）

## 产物

- 环境：`yingxu-dev`（体验环境）
- 函数：`yingxu-agent-api`，部署目标 `$LATEST`
- 应用版本：`0.9.0-local-sync-preview`
- 本地构建包：`infra/cloud/function-packages/0.9.0-local-sync-preview-098ea858abd3.zip`
- SHA-256：`ffebe46a7d669394d4d5c90e216955faf7742bec40775d6d37cf03b5b892dac2`
- 清单回读：2738 个源码/依赖文件逐项匹配，60 个 Python 3.11 Linux 依赖；本证据不包含 Secret 值。

## CloudBase 验收

- 函数配置保存后，控制台状态为正常；认证模式为 CloudBase，签名配置、环境 ID、桌面 client 映射和精确来源策略已配置。API Key 通过控制台注入引用，未写入代码包。
- 函数在线测试 `GET /healthz`：200，`ok=true`，服务版本 `0.9.0-local-sync-preview`。
- 函数在线测试 `GET /readyz`：200，`ready=true`，`database=ready`，`authentication=configured`，`configurationErrors=[]`。
- HTTPS Gateway `POST /v1/auth/session/exchange`（不带 Authorization）返回 401 `AUTH_REQUIRED`。这确认 `/v1` 路由到新服务并拒绝匿名请求；不代表真实账号认证通过。
- PostgreSQL schema v11–v16 已在此前逐项应用并回读；本次 readiness 再次确认函数可使用要求的 schema。真实账号下的 CRUD/同步验收仍待桌面用户登录。

## 本地验证

- `python -m unittest discover -s cloud-service/tests -v`：272 项通过，130 项因 PostgreSQL/CloudBase 集成需显式开启而跳过。
- 未创建合成账号、未发送邮箱验证码、未使用真实用户照片、未运行写入型账号流程。

## 限制和后续

本次完成了 CloudBase 环境配置、schema v16 函数部署及匿名拒绝/readiness 基础验证。真实邮箱或用户名密码登录、短会话交换、用户隔离 PostgreSQL 读写、WebSocket/断网恢复仍需真实桌面客户端登录后验收。功能验收完成后轮换本轮暴露过的服务端 CloudBase API Key 和 `SESSION_PRIVATE_KEY`；不记录密钥值。
