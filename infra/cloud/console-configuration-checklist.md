# CloudBase 控制台配置清单（待用户操作）

适用环境：`yingxu-dev`。此清单只描述待配置项；本地代码准备和 schema v16 回读不代表这些项目已配置、函数已部署或登录已验收。不要把任何密钥值写进仓库或聊天。

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
- `JWT_ISSUER`：正式 HTTPS issuer URL。
- `JWT_AUDIENCE`：影序服务端短会话的 audience 标识。
- `SESSION_KEY_ID`：当前签名密钥的非秘密 key ID。
- `DATABASE_URL`：留空，当前部署候选通过 CloudBase PostgreSQL REST 访问共享数据库。

### 通过 Secret/加密环境变量设置

- `CLOUDBASE_ENV_ID`：`yingxu-dev` 的环境 ID。
- `CLOUDBASE_APIKEY`：该环境服务端使用的 CloudBase API Key。
- `SESSION_PRIVATE_KEY`：影序短会话签名私钥，使用项目支持的 PEM 格式；需与 `SESSION_KEY_ID` 配对。
- `SESSION_VERIFICATION_JWKS`：只有在密钥轮换期间需要同时验证旧公钥时设置；按当前代码要求提供 JSON JWKS。

`MODEL_OWNER_CREDENTIALS` 仅供用户明确启用服务端 BYOK/模型路由时使用，不是本地 Agent 或基本身份认证的部署前置项；当前不需要录入模型供应商密钥。不要设置开发用 `DEV_AUTH_TOKEN` 到生产环境。

## 四、配置后的控制台自查

1. 核对函数仍未发布，先保存配置，不点击部署或发布。
2. 回看变量名和 origin/client ID 映射，不在截图或工单中暴露密钥值。
3. 确认桌面请求走 Rust 原生 HTTPS；`CORS_ORIGINS` 只有精确 Tauri origins 且无通配符；issuer 使用 HTTPS。CloudBase 安全来源不需要添加本地 Tauri 域名。
4. 确认邮箱验证码真实收件流程可用；不要为验证而向他人发送验证码。
5. 邮箱验证码与用户名/密码保持启用；短信与微信登录保持关闭。
6. 配置完成后告知维护者“登录方式和函数变量已配置”，不要发送 Secret 值。后续再单独进行本地包复核、云端部署授权和部署后验收。

## 当前需用户在控制台确认的项目

- 先轮换 `yingxu-dev` 当前注入给函数的 CloudBase API Key，再选用新 Key 注入 `yingxu-agent-api`。原因：控制台编辑表单将 Key 明文提供给辅助功能树，本轮未保存修改或部署；不要把新 Key 发到聊天、截图或仓库。
- 邮箱代发的实际收件投递仍待真实邮箱登录验收；本轮没有发送验证码。
- `yingxu-dev` 环境 ID 与服务端 API Key。
- 正式 client ID 到 `desktop`/`mobile` 的映射。
- HTTPS issuer、audience、会话签名私钥和 key ID。
- 桌面端的精确 Tauri CORS origins；移动端来源留待朋友交付真实 origin 后再添加。

所有秘密由用户留在控制台；助手不需要接收或读取其值。
