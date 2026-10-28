# CloudBase 只读状态核对（2026-10-27）

## 观察来源与范围

- 来源：已登录 Safari 中的 CloudBase 控制台；本次只读查看，不提交 SQL、不修改云函数/环境设置、不读取或记录密钥。
- 环境：`yingxu-dev`，ID `yingxu-dev-d8gowpye17ee27a61`，上海体验版。
- 函数页：列出 `yingxu-agent-api`，控制台状态“正常”，类型 HTTP 云函数，Python 3.11；描述仍称业务访问等待身份与数据库配置。
- PostgreSQL 页：可见应用数据表以及 `schema_migrations`。此前该会话在 2026-10-26 读取迁移表时记录了版本 1–10，最后时间为 `2026-10-26 01:24:28+00`。本次没有重新执行 SQL 或修改该数据。

## 与当前仓库的差异

- 从 GitHub Codex 分支 `codex/cloud-agent-store` 找回并同步 `cloud-service/` 与 `infra/cloud/`，包含 v1–v16 SQL、服务实现和测试；没有同步其桌面 UI/宿主文件，也没有更改 WorkBuddy 分支。
- CloudBase 控制台迁移历史此前读到 v10，可见 `agent_turns`、`agent_model_requests`、`deleted_agent_sessions`、`sync_invalidations`、`sync_owner_locks` 等表。该分支证据也记录线上 v10；迁移 v11–v16 仍未在 CloudBase 应用。
- 当前服务用到 v15 local-agent steps 和 v16 atomic action insert。已将 production readiness 从 v14 提升到 v16，避免代码在只具备 v14/v15 时报告 ready。
- 临时 PostgreSQL 17.11 socket-only cluster 按顺序及重复应用 v1–v16；268 项 Python 服务回归通过，12 项 CloudBase 实网测试按 opt-in 设计跳过。验证日志：`local-postgresql-v16-regression-20261027.log`，SHA-256 `60fdcf64840f1174e2b68cdf187d4f9f92fc1788a87690112d993110971c189f`。
- Python 3.11 Linux x86_64 HTTP 云函数上下文已本地构建并按 manifest/source/依赖检查：build ID `5d5bb5be36c6702ed1f6af61ae2b95e565e2ec06662ec53e1c4271514c6a37ef`，源码文件 2738 个，manylinux 依赖 60 个，schemaVersion 16，`sourceMatched=true`。构建目录在 Git 忽略目录 `infra/cloud/function-build/`；未制作部署 ZIP，因凭据配置未准备。
- 线上仍为 v10；先确认清理清单、逐条应用/回读 v11–v16，再构建和部署。控制台函数页显示“正常”不表示当前 v16 服务 readiness、身份或业务路径通过。

## 登录与部署边界

- CloudBase 控制台仍可查看，但本机 CLI 未确认有效登录。之前的设备码已过期；CLI 凭据刷新请求因本机域名解析超时失败。
- 本轮没有访问服务端密钥、没有读取本机可能存在的 API Key 文件、没有通过 API 写入云端、没有应用迁移或部署。
- 控制台“函数正常”只表示页面显示的运行状态，不证明 `/readyz`、JWT、数据库访问、业务 API 或 WebSocket 正常。

## 下一步门槛

1. 检查并清理证据清单中 4 个合成测试 owner，确认无残留。
2. 只读对照 CloudBase v10 与 v11–v16 SQL，再按序迁移并验证权限/RPC，回读 v16。
3. 由产品账号系统确认可信身份 issuer、audience、JWKS、客户端映射及会话刷新/撤销语义；配置最小权限服务端连接和 HTTPS CORS。
4. 重建 v16 function context，完成来源/依赖/敏感内容校验；有效 CLI 登录恢复后再做部署和回滚验收。
