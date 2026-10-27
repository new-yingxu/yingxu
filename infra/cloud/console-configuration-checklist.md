# CloudBase 控制台配置清单（待用户操作）

适用环境：`yingxu-dev`。此清单只描述待配置项；本地代码准备和 schema v16 回读不代表这些项目已配置、函数已部署或登录已验收。不要把任何密钥值写进仓库或聊天。

## 一、登录方式

1. 在 CloudBase 控制台切换到 `yingxu-dev`。
2. 打开「身份认证」中的登录方式设置。
3. 启用并配置邮箱验证码登录，确认发信配置和收件邮箱验证流程可用。
4. 启用用户名/密码登录。新用户注册仍先通过邮箱验证码验证，再设置用户名和密码。
5. 微信扫码保持暂停。
6. 短信登录先保持不可用状态；只有短信服务商、合规签名、模板审核和实际投递均完成后，才把它列为可用登录方式。

## 二、云函数环境变量与 Secrets

进入「云函数/托管」中 `yingxu-agent-api` 的服务端环境配置。先配置非秘密项，再通过控制台 Secret/加密环境变量入口录入秘密。密钥只在控制台生成或粘贴，勿放在客户端、ZIP、日志、命令历史或聊天中。

### 非秘密配置

- `APP_ENV=production`
- `AUTH_MODE=cloudbase`
- `CORS_ORIGINS`：仅填写正式桌面/移动客户端的 HTTPS origin，逗号分隔，不使用 `*`；如客户端使用自定义协议，先确认当前服务 CORS 层是否需要该 origin，不要臆造网址。
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

## 三、配置后的控制台自查

1. 核对函数仍未发布，先保存配置，不点击部署或发布。
2. 回看变量名和 origin/client ID 映射，不在截图或工单中暴露密钥值。
3. 确认所有客户端 origin 都是 HTTPS 且无通配符；issuer 使用 HTTPS。
4. 确认邮箱验证码真实收件流程可用；不要为验证而向他人发送验证码。
5. SMS 和微信保持暂停，直至各自的业务决策及配置条件满足。
6. 配置完成后告知维护者“登录方式和函数变量已配置”，不要发送 Secret 值。后续再单独进行本地包复核、云端部署授权和部署后验收。

## 当前需用户在控制台填写的项目

- 邮箱验证码通道是否已启用并能正常投递。
- 用户名/密码登录是否已启用。
- `yingxu-dev` 环境 ID 与服务端 API Key。
- 正式 client ID 到 `desktop`/`mobile` 的映射。
- HTTPS issuer、audience、会话签名私钥和 key ID。
- 桌面/移动端 HTTPS CORS origins。

所有秘密由用户留在控制台；助手不需要接收或读取其值。
