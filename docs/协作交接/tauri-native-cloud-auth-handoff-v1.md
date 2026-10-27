# Tauri 原生 CloudBase 登录与云请求交接 v1

## 目标

桌面端继续使用配置的 HTTPS 自定义 API 域名，但由 Tauri Rust/sidecar 原生网络层发出 CloudBase 身份认证和影序云服务请求。Vue WebView 只通过受限 Tauri IPC 调用，不直接 `fetch` 云端。以此避免将 Windows WebView 的 `http://tauri.localhost` 加入 CloudBase 浏览器安全来源列表。

## 已确认事实

- 用户已授权按此方案开始实施。
- Windows Tauri 生产页面来源为 `http://tauri.localhost`；WebView 直连仍会触发浏览器 CORS/安全来源校验。
- CloudBase 官方 HTTP Auth API 支持用户名/邮箱密码登录，并返回用户 access/refresh token；邮箱验证码接口也有 HTTP API。
- 当前真实登录尚未连接；现有 UI/adapter 有 mock 或 `CLOUD_ADAPTER_NOT_CONNECTED` 边界。
- 不得将管理员 API Key、服务端密钥或用户密码持久化/打包到客户端。用户令牌存入系统凭据存储。

## 实施边界

1. 在 `codex/cloud-agent-store` 分支实现；不得修改 `workbuddy/dev`、main 或 Windows/移动验收分支。
2. 为 CloudBase Auth REST 与影序云 REST 提供 Rust/sidecar 原生 HTTPS 客户端；服务目标固定为项目配置中的 CloudBase/API 自定义域名，不接受前端任意 URL。
3. WebView 通过最小权限 Tauri command/IPC 发起认证和云请求。密码、验证码、token 不写日志、不进入 LocalStorage；认证失败应清理临时敏感值。
4. access/refresh token 使用系统钥匙串/凭据存储；影序后端短会话仍由服务端验证 CloudBase 用户 token 并签发，不跳过认证。
5. 如果现有结构无法安全保存 token 或实现 HTTPS 域名约束，先记录阻塞和替代方案，不打包管理员凭据。
6. 本轮只做源码实现和本地验证，不连接真实 CloudBase、不发送验证码、不用真实账号、不做线上部署或数据库操作。需要真实环境验证时先向用户报告缺少的公开配置项或授权步骤；绝不索取密钥/密码到聊天。

## 小步验收

- HTTP transport：HTTPS-only、固定主机 allowlist、非允许方法/路径拒绝、超时与错误脱敏。
- Auth：密码登录、邮箱验证码登录所需请求、令牌刷新/登出；每个请求均用 mock HTTP server 验证，断言无凭据日志及无重复请求。
- Token 存储：OS 凭据接口的读写/清除用 mock 验证；不得回退到 Web Storage 或明文文件。
- Cloud client：WebView IPC → native HTTP transport；影序 API 仍校验短会话。
- 检查 Windows origin 未加入远端安全来源依赖；确认本地备份、本机 Agent 未受影响。
- 运行受影响的定向测试与前端构建；报告命令、结果及未覆盖的真实 CloudBase/Windows 验收。

## 交接约定

实现者完成后向主指挥线程汇报文件、接口、测试结果、分支提交 SHA、真实云端待办。不要合并主线、发布或部署。
