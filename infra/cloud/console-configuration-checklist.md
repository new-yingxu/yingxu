# CloudBase 控制台配置清单（待用户操作）

适用环境：`yingxu-dev`。此清单只描述待配置项；本地代码准备和 schema v16 回读不代表这些项目已配置、函数已部署或登录已验收。不要把任何密钥值写进仓库或聊天。

## 一、登录方式（控制台已核实，2026-10-27）

1. 在 CloudBase 控制台切换到 `yingxu-dev`。
2. 左侧「身份认证」→「登录方式」，页面路由为 `#/identity/login-manage`。
3. 当前实况：短信验证码“未启用”；邮箱验证码“已启用（代发）”；用户名密码“已启用”；微信开放平台登录“未启用”。与首期只使用邮箱验证码及用户名/密码的决定一致。
4. 新用户注册仍先通过邮箱验证码验证，再设置用户名和密码。

## 二、安全来源（桌面 WebView，控制台入口待核实）

CloudBase 官方文档说明，Web SDK 来源需要在环境安全来源中白名单登记；但用户 2026-10-27 的当前控制台截图显示「环境管理 → 安全管控」，页面只有云函数限额、云托管限额、OPA 策略鉴权，左侧没有“安全来源”菜单。不要在该限额页面修改任何内容；先确认当前控制台的实际入口。

需要配置时，Tauri 打包版按平台使用不同来源，精确允许：

- macOS：`localhost`（页面来源 `tauri://localhost`）
- Windows：`tauri.localhost`（页面来源 `http://tauri.localhost`）

官方文档称 `localhost` 默认安全来源；找到入口后再核对。不要添加 `*`。只在实际缺少时补 `tauri.localhost`。本地 Vite 开发的 `127.0.0.1:5173` 后续需要单独用于开发验收时再添加，不作为生产域名。桌面端 API CORS 同时要求把实际 Tauri 来源原样放入函数的 `CORS_ORIGINS`。

## 三、云函数环境变量与 Secrets

进入「云函数/托管」中 `yingxu-agent-api` 的服务端环境配置。先配置非秘密项，再通过控制台 Secret/加密环境变量入口录入秘密。密钥只在控制台生成或粘贴，勿放在客户端、ZIP、日志、命令历史或聊天中。

### 非秘密配置

- `APP_ENV=production`
- `AUTH_MODE=cloudbase`
- `CORS_ORIGINS`：只填确切来源，逗号分隔，不使用 `*`。桌面打包版按实际平台填 `tauri://localhost` 和/或 `http://tauri.localhost`；网页版只填真实 HTTPS 域名。不要把 Vite 开发地址作为生产来源。
- `CLOUDBASE_CLIENT_ORIGINS`：JSON 对象，将已登记的 CloudBase client ID 映射到 `desktop` 或 `mobile`，例如结构 `{"<desktop-client-id>":"desktop","<mobile-client-id>":"mobile"}`；替换占位符为控制台实际 client ID。
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
3. 确认 Web 来源为真实 HTTPS 域名；桌面只允许上文列明的精确 Tauri 来源且无通配符；issuer 使用 HTTPS。
4. 确认邮箱验证码真实收件流程可用；不要为验证而向他人发送验证码。
5. 邮箱验证码与用户名/密码保持启用；短信与微信登录保持关闭。
6. 配置完成后告知维护者“登录方式和函数变量已配置”，不要发送 Secret 值。后续再单独进行本地包复核、云端部署授权和部署后验收。

## 当前需用户在控制台确认的项目

- 邮箱代发的实际收件投递仍待真实邮箱登录验收；本轮没有发送验证码。
- 安全来源入口：官方文档写作「环境配置 → 安全来源」，但当前控制台「环境管理」下显示「安全管控、API Key 配置、环境设置」等入口，安全管控页本身是限频/OPA配置，并无安全来源列表。暂不要求添加域名，请先核对「环境设置」页面是否包含安全来源或切换配置页面。
- `yingxu-dev` 环境 ID 与服务端 API Key。
- 正式 client ID 到 `desktop`/`mobile` 的映射。
- HTTPS issuer、audience、会话签名私钥和 key ID。
- 桌面端的精确 Tauri CORS origins；移动端来源留待朋友交付真实 origin 后再添加。

所有秘密由用户留在控制台；助手不需要接收或读取其值。
