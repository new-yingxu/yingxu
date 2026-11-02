# CloudBase 账号统一：移动端迁移说明（2026-11-02）

## 结论

影序 Pocket（移动端）已从 Firebase 迁移到 **CloudBase + 影序身份服务**，与桌面端共用同一套账号体系。一个影序账号可同时在 Windows 桌面、macOS 桌面、Android 手机登录，云端同步按账户聚合。

## 迁移内容

| 旧实现 | 新实现 |
|---|---|
| `firebase-auth.ts`（Firebase Auth 邮箱密码 + 硬编码 apiKey） | `cloudbase-auth.ts`（门面）+ `yingxu-cloud.cjs`（CloudBase HTTP API） |
| `firebase-sync.cjs`（RTDB 文档存储 + SSE 实时流） | `yingxu-cloud.cjs`（/v1/sync 事件日志 + 轮询折叠） |
| `users/{uid}/history、live` 文档 | `job.snapshot.v1` 同步事件（contracts/sync-event-v1.schema.json） |

- 移动端认证协议与桌面 Rust 宿主（`cloud_auth.rs`）完全一致：signin / signup（邮箱验证码）/ token 刷新（一次性轮换）/ signout。
- 登录后用 CloudBase 账号令牌向影序身份服务换取 15 分钟设备短会话（`origin=mobile`，由服务端 `cloudbase_client_origins` 的 client 映射决定，客户端声明不参与判定）。
- 凭证只存系统安全存储（SecureStore / keyring），代码与仓库不内嵌任何密钥；Firebase 的硬编码 apiKey 已随迁移删除。

## 隐私边界（与仓库 README 一致）

同步到云端的只有 **job.snapshot.v1 任务摘要**：任务名、状态、文件数、字节数、进度、各目标计数。文件名、原片路径、笔记、坐标不再上云（旧 Firebase 同步曾包含文件名，本次迁移一并收紧）。

## 实时性说明

影序同步 API 是拉取式事件日志，移动端 `watch()` 以 5 秒轮询近似旧的 Firebase 实时推送；多端看到彼此进行的延迟约 5–10 秒。

## 上线前需要完成的配置

1. CloudBase 控制台：为移动端创建独立 Client（发布 key），加入 `yingxu-dev` 环境并开启邮箱认证。
2. 影序身份服务：`cloudbase_client_origins` 增加该移动端 client 并映射 `origin=mobile`。
3. EAS 构建注入环境变量：`EXPO_PUBLIC_CLOUDBASE_AUTH_ORIGIN`、`EXPO_PUBLIC_CLOUDBASE_CLIENT_ID`、`EXPO_PUBLIC_IDENTITY_API_BASE_URL`。
4. 桌面端：Rust 侧已加会话静默续期（`cloud_api.rs::refresh_session` + `cloud_auth.rs::refresh_access_token`），需在有 Rust 工具链的环境跑 `cargo test` 并重新出包。

## 遗留与验证状态

- `source` 测试 30/30 通过（含新增 `tests/yingxu-cloud.test.cjs`）。
- `tsc --noEmit` / `expo lint` 需在安装完整依赖的环境执行（本机未装 node_modules）。
- Rust 改动未经本地编译（本机无 cargo/MSVC），以 CI 或协作机 `cargo check && cargo test` 为准。
- 桌面 browser 通道（`@cloudbase/js-sdk`）与原生通道并存：原生通道为打包版主路径，浏览器通道仅开发预览。
