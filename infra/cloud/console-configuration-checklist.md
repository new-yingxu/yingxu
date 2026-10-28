# CloudBase 控制台配置清单（真实账号验收待完成）

适用环境：`yingxu-dev`。函数配置与 schema v16 已部署；本清单用于记录当前配置和真实账号验收剩余项。不要把任何密钥值写进仓库或聊天。

2026-10-28 更新：已为 `yingxu-agent-api` 保存 CloudBase API Key 注入引用与认证配置，并部署当前 `0.9.0-local-sync-preview` 函数包到 `$LATEST`。函数状态正常；控制台测试 `/healthz`、`/readyz` 均为 200，Gateway 无凭据 session-exchange 返回 401 `AUTH_REQUIRED`。本次辅助功能输出暴露了服务端 CloudBase API Key 与 `SESSION_PRIVATE_KEY` 明文；本阶段功能验收后轮换这两类凭据。值不应进入仓库、聊天或日志。

## 一、登录方式（控制台已核实，2026-10-27）

1. 在 CloudBase 控制台切换到 `yingxu-dev`。
2. 左侧「身份认证」→「登录方式」，页面路由为 `#/identity/login-manage`。
3. 当前实况：短信验证码“未启用”；邮箱验证码“已启用（代发）”；用户名密码“已启用”；微信开放平台登录“未启用”。与首期只使用邮箱验证码及用户名/密码的决定一致。
4. 新用户注册仍先通过邮箱验证码验证，再设置用户名和密码。

## 二、桌面网络与来源配置（已按原生 HTTPS 方案完成）

桌面端 CloudBase 登录和影序 API 请求都由 Tauri Rust 宿主通过 HTTPS 发出，WebView 不直接调用这些服务，也不使用 CloudBase Web SDK。因此：

- 不需要把 `tauri.localhost` 或 `tauri://localhost` 添加到 CloudBase「安全来源」；无需为此升级套餐。
- Windows 与 macOS 共用同一个影序 HTTP Gateway API origin：控制台显示的 `https://yingxu-dev-d8gowpye17ee27a61-1479513274.ap-shanghai.app.tcloudbase.com`。构建变量不带 `/v1`；HTTP Gateway 已配置 `/v1` 路由与路径透传。
- CloudBase 账号认证走其官方 HTTPS HTTP API origin：`https://yingxu-dev-d8gowpye17ee27a61.api.tcloudbasegateway.com`，client ID 默认使用环境 ID `yingxu-dev-d8gowpye17ee27a61`。这与影序 API Gateway 是不同用途的两个 HTTPS host，不应混为一个域名。
- `CORS_ORIGINS` 是函数自己的浏览器来源响应策略，不是 CloudBase 安全来源登记。当前桌面请求由原生 HTTP 发出，不靠 CORS 放行；服务端配置仍使用代码接受的精确 Tauri origins `tauri://localhost,http://tauri.localhost`，禁止通配符。若将来有网页直接访问 API，再单独加入其真实 HTTPS origin。

已在 2026-10-28 用控制台保存并读回 `/v1` → `yingxu-agent-api`、路径透传开启；网关 CORS 与网关级鉴权保持关闭，由应用验证 Bearer 身份。未添加安全来源、未升级套餐。此方案同时适用于 macOS 和 Windows。

## 三、云函数环境变量与 Secrets

进入「云函数/托管」中 `yingxu-agent-api` 的服务端环境配置。先配置非秘密项，再通过控制台 Secret/加密环境变量入口录入秘密。密钥只在控制台生成或粘贴，勿放在客户端、ZIP、日志、命令历史或聊天中。

### 非秘密配置

- `APP_ENV=production`
- `AUTH_MODE=cloudbase`
- `CORS_ORIGINS=tauri://localhost,http://tauri.localhost`：只允许两个精确桌面 WebView origin；不使用 `*`。桌面流量经 Rust 原生 HTTPS，不依赖浏览器 CORS。
- `CLOUDBASE_CLIENT_ORIGINS={"yingxu-dev-d8gowpye17ee27a61":"desktop"}`：CloudBase 官方 HTTP API 的 client ID 默认是环境 ID；只有控制台实际配置了另一个 client ID 时才替换。移动端 client ID 等移动端接入时再登记。
- `JWT_ISSUER=https://yingxu-dev-d8gowpye17ee27a61-1479513274.ap-shanghai.app.tcloudbase.com`：当前体验环境先使用已配置的 HTTPS API Gateway origin 作为 issuer；正式生产前应切换到影序自有稳定 HTTPS 域名，并保持签发与校验一致。
- `JWT_AUDIENCE=yingxu-agent-api`：影序服务端短会话的 audience 标识。
- `SESSION_KEY_ID=yingxu-dev-20261028-01`：本轮体验环境签名密钥的非秘密 key ID。
- `DATABASE_URL`：留空，当前部署候选通过 CloudBase PostgreSQL REST 访问共享数据库。

### 通过 Secret/加密环境变量设置

- `CLOUDBASE_ENV_ID`：`yingxu-dev` 的环境 ID。
- `CLOUDBASE_APIKEY`：该环境服务端使用的 CloudBase API Key。
- `SESSION_PRIVATE_KEY`：影序短会话签名私钥，使用项目支持的 PEM 格式；需与 `SESSION_KEY_ID` 配对。
- `SESSION_VERIFICATION_JWKS`：只有在密钥轮换期间需要同时验证旧公钥时设置；按当前代码要求提供 JSON JWKS。

`MODEL_OWNER_CREDENTIALS` 仅供用户明确启用服务端 BYOK/模型路由时使用，不是本地 Agent 或基本身份认证的部署前置项；当前不需要录入模型供应商密钥。不要设置开发用 `DEV_AUTH_TOKEN` 到生产环境。

## 四、当前状态与待验收

1. 函数配置已保存并部署；源码包、环境变量名称、HTTPS issuer/audience、桌面 client 映射和精确来源策略已配置。`DATABASE_URL` 留空，数据库由 CloudBase API Key 注入方式访问。
2. 邮箱验证码和用户名/密码保持启用；短信与微信不纳入首期。
3. CloudBase 邮箱验证码实际投递、桌面真实账号登录与 session exchange 尚未验收。登录凭据须由用户在桌面应用中自行输入。
4. 登录后还需验证该账号的数据库读写、备份记录/Agent 会话同步和断网恢复；不创建合成测试账号。
5. 完成阶段功能验收后，轮换 CloudBase 服务端 API Key 与影序 session signing private key，并更新函数配置；轮换值不发送给助手。

## 注意事项

- CloudBase API Key 与影序 session signing private key 已配置在控制台，不能复制进源码或版本库。
- 旧 `yingxu-codex-dev` API Key 已过期，尚未删除。
- 当前只配置 `desktop` client 映射；移动端身份接入由移动端工作流单独处理。

所有秘密由用户留在控制台；助手不需要接收或读取其值。
