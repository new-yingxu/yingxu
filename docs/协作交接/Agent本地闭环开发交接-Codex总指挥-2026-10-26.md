# 影序本地 Agent 闭环开发交接（2026-10-26）

> 接收对象：Agent 开发会话。分支同步完成后，用户已授权继续本地 Agent 闭环开发。先完成本地 LangGraph/model/tool/桌面协议和安装包验收；CloudBase 生产请求、迁移和部署暂缓至本地闭环之后。

## 分支与负责范围

- 工作分支：`codex/cloud-agent-store`，隔离 worktree：`/Users/nanyu/Documents/AI工具/02_Codex数据_0907/worktrees/yingxu-cloud-agent/new-影序agent开发`。该分支已推送 GitHub；远端与本地分支头已回读一致。
- 当前阶段职责：本地 LangGraph/runtime、本地模型 provider、Agent 工具 loop、持久步骤/恢复逻辑及其桌面宿主协议。云端 Agent、账号、CloudBase 和部署排到本地闭环之后。
- 只修改该 worktree；不要直接写 `backend/**`、WorkBuddy 的 `desktop-ui/**`，或另一人的工作树。接口变化先在此分支更新本交接文档和协议请求。

## 与总指挥/WorkBuddy 的分工

- WorkBuddy 负责 Vue UI、交互状态和前端 adapter，分支 `workbuddy/dev`。
- Codex 总指挥负责 SQLite/FTS5、本地备份内核、Tauri 宿主与集成验收，分支 `codex/yingxu-desktop-agent`。
- Agent 开发负责本地模型和 LangGraph 调度，向 Codex 提交完整请求/响应、状态、错误、恢复/幂等语义及测试证据；向 WorkBuddy 只提交稳定的 UI/adapter 字段契约。
- 任何联调都通过对应分支内的 Markdown 交接文档和提交记录，不靠聊天口头字段约定。

## 本地阶段顺序

1. 把现有本地 Agent/runtime 代码、测试、数据库迁移和证据盘点成已完成/未完成表；明确哪些是真实本地模型调用，哪些是 fixture 或隔离服务。
2. 先冻结本地协议：模型状态、建 turn、工具请求、待本机确认提议、确认/拒绝、结果回执、重复请求、显式恢复、失败和不确定结果。密钥只保留在本机受控存储/进程内，不进入 UI、日志或安装包。
3. 每个小片先写状态与契约，再实现并即时测试。第一闭环优先只读本地查询；之后才接用户确认式打开/改名/移动/清理等副作用。
4. 对每个真实本地模型用例记录模型标识、输入边界、工具调用链、执行时长和实际返回；禁止把云端请求跳过写成通过。
5. 本地 DB、模型、Agent、宿主和 UI 联通并有回归记录之后，才整理独立的云端阶段计划。

## 同步后恢复状态与本地 sidecar 契约

分支同步之后，`codex/cloud-agent-store` HEAD 为 `1d88997ed16c3f9f994cf620c502bc5a4bd72d3b`；此前已有的本地云服务/Agent改动仍在工作树，未夹带到同步提交中。用户已授权继续。当前有独立本地 PostgreSQL 迁移/回归基线：v1–v16 顺序应用并重放尾部迁移，268 项通过，12 项 CloudBase opt-in 跳过；日志保留在 `cloud-service/evidence/local-agent-local-postgresql-regression-20261026.log`。这不是 CloudBase 生产环境证据。

当前 sidecar 在本地采用 `POST /v1/agent/local-turns` 和 `POST /v1/agent/local-turns/{sessionId}/{turnId}/results`；显式 `useModel: true`。只开放 `search_media`、`get_metadata`、`get_backup_job` 和 `open_item`；模型请求不获执行授权，由宿主在当前设备上执行只读工具后回送 typed result。`open_item` 必须在本机确认，成功回执要求 `mediaId`、`opened: true` 和有界 `receiptId`。本地 sidecar 拒绝 `rename_item` / `move_item`，因为当前 UI/宿主尚无这两个操作的完整预览、确认与回执链。每轮最多3次模型请求、每次最多2个工具调用、每轮摘要最多10条。pending/uncertain 不自动重发供应商请求。

sidecar 运行环境约定：绑定 `127.0.0.1` 随机端口，runner stdout 发 `{event:"ready",host,port}`；宿主设置 `YINGXU_LOCAL_AGENT_MODE=1`、新建高熵 `YINGXU_LOCAL_AGENT_TOKEN`、本机私有 `YINGXU_LOCAL_AGENT_DATABASE_URL=sqlite:///...` 和本地 Ollama 模型名。`GET /v1/agent/local-status` 也需要该 host token，返回 `configured`、provider/model/region、`availability`、显式同意、无执行授权和 `cloudFallback:false`；探测固定访问 `127.0.0.1:11434/api/tags`，禁用代理且2秒超时，只匹配配置模型，不探测任何云端服务。宿主用相同 token 请求 `POST /_host/shutdown`，runner 设置 Uvicorn `should_exit` 并等待优雅退出。打包运行时稳定 RSA signing key 必须由桌面宿主从 OS credential store 提供，通过 `YINGXU_LOCAL_AGENT_SIGNING_KEY` 与 `YINGXU_LOCAL_AGENT_KEY_ID` 注入；sidecar 不得读取宿主的云端模型凭据、CloudBase API key 或其签名私钥。

本地生产配置隔离已新增：即使父进程 `APP_ENV=production`，独立 sidecar 也会强制本机 SQLite、Ollama、独立 issuer/audience，并剥离 CloudBase 与远端模型配置；生产模式缺少宿主稳定签名 key 时启动失败。协议/app/provider聚焦测试当前26项通过；真实 Ollama测试通过，实际验证model → `search_media` typed result → model，重放不增加模型请求；若模型继续请求工具，最多继续到3次后有界收敛。待处理continuation已用隔离SQLite和稳定签名key跨ASGI app重启恢复并验证幂等。

最新本地 arm64 sidecar 已由 PyInstaller 6.22.3 以 one-folder 形式构建，路径 `/tmp/yingxu-sidecar-macos-arm64-20261026-r3/yingxu-local-agent`；目录 94,555,663 字节，manifest 记录 1,187 个文件，主执行文件 SHA-256 为 `73c5313c6425927798eb11ce3442a677c37d4e1f3a496c51084cad2a4c2b4d82`。包未签名/公证，不含模型权重。生产父进程 smoke 已验证随机 loopback 端口、`/healthz`、本地 Ollama 状态探测、Bearer 保护的优雅关闭，进程以 exit 0 退出。随后直接运行此打包 sidecar 接真实本机 Ollama：2 次模型请求完成合成 `search_media` typed result 与重放校验；工具结果是测试夹具，没有读取真实媒体数据库或执行文件操作。首次包内模型提示直接回答未进入工具，已保留该次脱敏失败记录，改用已验证提示后 smoke 通过。重复待处理 continuation 已通过保持 SQLite 文件和稳定 signing key、重启 ASGI app 的测试。验收器现在会在启动前校验 manifest 路径安全、重复 JSON key、文件集合、每个文件大小及 SHA-256，并拒绝 manifest 或 bundle 文件逃出根目录；11 项构建暂存/清单验收单测通过，r3 原包清单校验及打包 Ollama smoke 复验通过。暂存器也会在复制到 Tauri 资源前拒绝不匹配的包并避免递归复制；完整 94,555,663 字节 r3 临时暂存与 1,187 文件回读通过。清单复验证据在 `cloud-service/evidence/local-agent-sidecar-manifest-verification-r3-20261026.json`。可重跑验收入口为 `infra/desktop_agent/verify_sidecar.py --bundle-dir <bundle> [--with-ollama]`。资源暂存命令为 `python infra/desktop_agent/build_sidecar.py --output-dir /tmp/yingxu-sidecar-<platform>-<build-id> --stage-to backend/tauri-host/agent-sidecar/yingxu-local-agent`；目标目录已存在时会拒绝覆盖，并复制完整 one-folder（含 `_internal/**` 和 manifest）。Windows 必须在 Windows 构建，manifest entrypoint 为 `yingxu-local-agent.exe`；当前仅通过平台文件名/暂存逻辑单测，未生成或运行 Windows 包。证据分别在 `cloud-service/evidence/local-agent-sidecar-packaged-smoke-20261026.json` 与 `cloud-service/evidence/local-agent-sidecar-packaged-ollama-20261026.json`。

## Tauri macOS DMG 本地构建

桌面宿主会话提交 `7ab8048` 已接入 sidecar 生命周期、OS keychain signing key 注入及 status/turn IPC；宿主侧5项测试通过，另有真实 Ollama + 隔离 SQLite 索引查询结果往返记录。使用本机 SDK15.4 执行 `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk cargo tauri build --bundles dmg`，得到基础集成包 `影序桌面开发版_0.2.0-draft.1_aarch64.dmg`（50,529,475 bytes，SHA-256 `77152b0039ba94e43b53a812d5b5bf6c3ca41f8b44e13127cabacc2996947ba7`）。之后临时叠加全栈 UI 源码构建得到 `影序桌面开发版_0.2.0-draft.2-local-agent-ui.dmg`，50,538,324 bytes，SHA-256 `4e14db53325f0c9f5838b4f86a3e063eb81d15cbaf30dc7eaa95f13a13ba6bea`。旧包 CRC、ad-hoc codesign、sidecar manifest 1,187 文件完整性及8类常见凭据扫描通过；扫描器非穷尽。前序 `0c26e54` Ollama 安装引导和新提交 `d5596c7fc085b570fa112b8ede98accd8694a303` 本机模式 UI 均不在 draft.2。新 UI 10项定向检查与完整 `pnpm build` 通过，入口 native-only/session=null/云动作零调用。宿主提交 `71a7aee` 的 Finder-DMG 实测只到登录首屏，Agent 页未进入，不能算 WebView→sidecar→SQLite 验收；旧隔离 app/DMG 已退出卸载，无 CloudBase 请求。证据：`cloud-service/evidence/tauri-macos-dmg-local-agent-package-20261026.json` 及 host 分支 `backend/evidence/tauri-macos-isolated-ui-attempt-20261026.json`。下一步由宿主收尾失败/重启测试，在本地叠加已完成 UI 与引导后重建新版 DMG并完成真实端到端验收；正式 Developer ID 签名/公证未做。

只对本任务拥有的文件进行提交，不得夹带其他 worktree 改动。全栈已提交本机模式 `d5596c7`，10项定向检查与 `pnpm build` 通过；Ollama 安装引导 `0c26e54` 与本机模式都需纳入新的 macOS DMG。宿主新增提交 `fca151f`（`backend/tauri-host/src/main.rs`、Cargo 测试依赖、宿主回归证据及交接记录），其远端与本地一致；该提交包含产品代码，已超过先前仅推送证据的边界，后续不再推产品源码、不做 force-push/回滚。宿主7/7回归及 cargo check/fmt通过；真实 Ollama、合成索引工具回路、跨sidecar重启显式恢复、幂等与 uncertain 状态重启不重放通过。本地 sidecar HTTP/状态/工具/跨 ASGI 重启/真实Ollama定向套件11/11通过。新一轮宿主任务正把两项全栈本地 commit 临时叠加到隔离构建输入，重建 DMG 并验 Agent 页内 WebView→Tauri→sidecar→隔离 SQLite→真实 Ollama。仍未完成 packaged UI 故障注入全链路、macOS Developer ID签名/公证、Windows x64构建运行。模型权重和 Ollama 安装不随约95 MB sidecar发布。当前不调用 CloudBase、不部署、不提交云端 schema。
