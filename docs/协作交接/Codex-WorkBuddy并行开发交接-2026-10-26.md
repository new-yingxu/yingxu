# 影序并行开发与阶段交接（2026-10-26）

> 本文是 Codex、WorkBuddy、Agent 开发会话之间的分工和开发顺序交接。任何开发人员、软件会话、分支或阶段交接，都要在接收方所在分支提交交接文档；聊天消息只通知文档路径，不代替文档。用户已明确授权在分支同步后开始开发；各角色只修改自己的分支和文件范围。

## 用户确认的规则

- Codex 与 WorkBuddy 使用不同 Git 分支并行开发，互不覆盖；所有代码只提交分支，不推主线。最终验收和部署负责人由用户指派。
- 先完成桌面本地数据库、本地 Agent、本地模型和本地文件闭环，再进入云端 Agent、CloudBase 账号、数据库和跨端同步。
- 每个小片先确定 UI 状态与接口契约，再开发并立即测试；失败先修复再进入下一片。
- 移动端开发与 Windows 测试由用户的同学负责，Codex 不负责这两项实现和平台验收。
- 交接文档和对应代码、测试证据一并提交至各自分支。不得将未提交工作树描述为已交付。

## 分支与文件所有权

| 负责人 | 本地分支 | 唯一负责范围 |
|---|---|---|
| Codex 总指挥 | `codex/yingxu-desktop-agent` | 桌面 Rust/SQLite 本地内核、FTS5、备份/校验/报告/任务状态、Tauri 本地宿主、接口契约与验收统筹。主要范围：`backend/core/**`、`backend/tauri-host/**`、本分支的验收脚本与证据。 |
| WorkBuddy 全栈开发 | `workbuddy/dev` | 桌面 Vue 页面、状态流、组件、前端 adapter 与 UI 测试。主要范围：`desktop-ui/src/**`、`desktop-ui/tests/**`。不改 Rust/云端服务。 |
| Agent 开发会话 | `codex/cloud-agent-store` 独立 worktree | 当前阶段负责本地 LangGraph/runtime、本地模型 provider、Agent 工具回合和与桌面宿主的契约交接。仅写自己的隔离 worktree；云端实现/部署放在本地闭环之后。 |
| 移动端同学 | `mobile/expo-v1` | Expo Android 与 iOS PWA 的移动端实现和对应设备记录。按该分支的移动端交接文档执行。 |
| Windows 测试同学 | `qa/windows-acceptance` | Windows x64 安装、启动和功能验收，只提交测试记录、环境信息、截图及复现材料。按该分支的 Windows 交接文档执行。 |

各分支基于独立已提交基线并保持隔离。Codex 分支不包含 WorkBuddy 脏工作树改动；Agent 分支的改动继续保留在独立 worktree，不搬运、不 cherry-pick、不合并。当前所有开发提交仍只进入各自分支，不进入 `main`。

## 开发顺序（已获用户授权开始执行）

0. **总指挥准备**：核对本地/远端分支、接口、文件所有权和交接证据；分支同步后按本顺序持续推进。
1. **WorkBuddy：桌面 UI 小片**。按页面定义扫描、预检、目标确认、备份进度、成功/部分完成/失败/取消等状态；提交 UI 行为和接口字段，配套 UI 检查结果。
2. **Agent 开发：本地 Agent 小片**。先给出本地 runtime/model/工具协议与字段映射，再完成模型往返、只读查询和结构化结果；写文件操作只能形成提议，等待本机确认，不绕过 Tauri 宿主。
3. **Codex：本地数据与备份内核**。顺序为设备/目录预检 → 用户确认后复制及 SHA-256 → 本地记录和可追溯报告 → 暂停/取消/恢复/重试 → 多个本地目标结果。每一片立即做对应测试，失败先修复。
4. **三方本地集成**。逐片把 UI、Tauri、本地 DB、Agent/runtime 与本地模型接起来；在隔离临时目录与合成素材中检查读写边界、确认/拒绝、错误和恢复；完成桌面构建与启动验证。只有对应结果和交接文档齐全，才进入下一片。
5. **云端阶段**。本地闭环过关后，再安排云端 Agent、CloudBase 部署/身份/账号/数据库和同步；密钥、账号授权或部署授权按用户规则停在授权点。
6. **独立平台工作**。移动端同学按 `mobile/expo-v1` 开发和测试；Windows 同学按 `qa/windows-acceptance` 做 x64 实测。平台记录不由 macOS 调试或交叉构建替代。

## 当前状态（2026-10-26，分支同步后开发中）

- 已推送主线和五条开发分支，并用 `git ls-remote --heads origin` 回读验证本地/远端分支头一致。为防止公开仓库历史暴露已删除的本机工作笔记，六条分支历史已清理并在最终文件树不变的前提下重写；`main` 为此次恢复源码快照。当前不提交未完成的工作树改动。
- WorkBuddy 已在 `workbuddy/dev` 开始 Agent UI adapter 小片：Agent 发送经本机逐次同意组件，adapter 行为检查 5 项、原生 adapter 检查 17 项通过，Vite 生产构建通过。完整 `pnpm build` 目前被并行新增的 `desktop-ui/src/api/backup-state.ts` 类型错误阻塞；WorkBuddy 正在隔离和记录该阻塞。相关改动尚未全部提交。
- Agent 在 `codex/cloud-agent-store` 独立 worktree 继续本地 LangGraph/Ollama sidecar 与桌面宿主契约；已有独立 HTTP 合约测试、真实 Ollama 工具往返和 loopback runner 记录。桌面包集成、Tauri spawn/stop 与可持久化 continuation key 仍待完成。云端部署和 CloudBase 联调仍排在本地闭环之后；其余尚未提交内容不得混入 Codex 或 WorkBuddy 分支。
- 外接卡 `/Volumes/SD_Card` 的本机实测已完成：只读索引 929/929 项，其中照片 784 项（DNG 495、JPG 289）；通用分析接口解码 DNG 495/495、JPG 288/289，剩余 1 张 JPG 触发受限内存上限并保持未分析；3 张缩略图读回检查通过。1 张 JPG 样本备份成功，目标校验与独立 SHA-256 一致；卡片源文件元数据在分析前后保持一致。证据：`backend/evidence/card-photo-check-ntfs-20261026.json`。
- 本次实卡测试还修正了验收脚本：改用 `library.analyze` 的类型分派路径，并按文件扩展名分批搜索，避免绕过 RAW 处理器及被单次 500 条结果上限截断。质量分类准确率尚未建立（没有人工标注集）；这不是 Windows、移动端或发行验收。
- 视频分析发现并修正：有内嵌封面时，原代码跳过实际视频抽帧；现在显式抽帧会同时分析封面和视频片段。卡片 44/44 段 4K MP4 元数据读取成功，44/44 均完成封面+关键帧分析（每段 4 帧：封面及 10%/50%/90%）；结果为 7 段 `review`、37 段 `unanalyzed`，候选信号为 `deep_shadow_candidate` 15 次，0 项分析异常。完整证据：`backend/evidence/card-video-check-ntfs-20261026.json`。该项仍是未校准的人工复核信号，不代表模型质量准确率。
- 为支持超过 500 条的本地媒体库，Codex 新增 RPC `library.searchPage`：请求 `query, kind, quality`，可选 `limit`（默认 200，允许 1–200）与 `offset`（默认 0）；响应 `{items,total,offset,limit,hasMore}`。旧 `library.search` 保留兼容，仍最多返回 500 条。WorkBuddy 后续接入媒体列表时应使用分页接口，并在条件变化时将 offset 重置为 0；不得在前端把 500 条旧接口结果当作完整列表。
- 分页小片验证：501 条合成媒体分三页（200/200/101）无遗漏、无重复；RPC 分页路由通过，201 条页大小拒绝。`cargo test -p yingxu-core` 64 项通过；`cargo build -p yingxu-core --release` 成功（Mac arm64，`SDKROOT=MacOSX26.5.sdk`）。WorkBuddy 分支的 UI 分页适配和 Windows 实测尚未完成。
- Codex 新增 Agent 本机打开操作的 Tauri 确认边界：只允许 `open_item`；预览前由宿主把模型给出的媒体 ID 解析到已索引普通文件，并检查用户授权的来源目录；预览 5 分钟内有效且 action ID 单次使用；用户拒绝时不访问文件，用户确认时再次校验索引指纹、路径和授权目录后才调用系统默认打开器。新增 IPC `agent_prepare_action` / `agent_confirm_action`，返回字段与 WorkBuddy adapter 的 `LocalOpenPlan` / `LocalActionReceipt` 对齐；不接受模型提供的路径或命令，同时移除无需二次确认的直接打开 IPC。
- 该宿主小片验证：`cargo fmt --manifest-path backend/tauri-host/Cargo.toml` 通过；在系统默认 MacOSX27.0 SDK 因 linker 不识别新架构标识而失败后，显式用 `SDKROOT=$(xcrun --sdk macosx26.5 --show-sdk-path)` 的 Tauri host 检查通过。扫描报告权限复核发现并修复 `backup.scanReport` 漏检用户目录 grant；它现在和普通扫描一样，必须先由原生目录选择器授权。宿主回归已增至 4 项，覆盖扫描报告授权、打开预览有效期、本机 Agent 能力白名单和实际打包 sidecar 状态/认证/优雅退出。首轮测试夹具误用了 macOS `/tmp` 符号链接路径，改到项目内临时目录后通过，未访问外接卡。
- Agent sidecar 的 r3 macOS arm64 onedir 包已通过 1,187 文件清单校验、authenticated status、真实 Ollama typed-tool 往返、replay 与优雅退出；包入口 SHA-256 为 `73c5313c6425927798eb11ce3442a677c37d4e1f3a496c51084cad2a4c2b4d82`，模型权重和 CloudBase 密钥未打入包。Agent 分支独立回归 268 项通过，12 项 CloudBase opt-in 按设计跳过；本轮无 CloudBase 请求。
- Codex 已接入 Tauri sidecar lazy start/status/turn/continuation/shutdown IPC：进程只绑定 `127.0.0.1` 随机端口；每次启动生成高熵 bearer token；RSA continuation key 放系统凭据库；Agent 使用独立应用私有 SQLite；只传当前桌面只读能力与打开提议，不启用云回退。Tauri 资源目录约定为 `backend/tauri-host/agent-sidecar/yingxu-local-agent/`，整个 onedir 目录都要暂存（可执行文件、`_internal/`、manifest）；生成包已在本机验证，按平台忽略且不提交。资源未暂存时主程序仍可启动，本机 Agent 状态显示不可用。
- 2026-10-26 本地模型与桌面宿主闭环增量：启用 `YINGXU_RUN_OLLAMA_HOST_TESTS=1` 后，真实 Ollama 模型从 Tauri host 启动已打包 sidecar，发出 `search_media` 请求；测试在隔离临时目录创建 `IMG_2048.jpg`，由 `Engine::index_source` 建立 SQLite 索引，再由 Rust `execute_read_tool` 返回索引结果给 sidecar，模型完成总结。重复提交 continuation 返回 `duplicate=true`，无用户媒体访问、文件变更、执行授权或云端回退。证据见 `backend/evidence/host-local-agent-ollama-roundtrip-20261026.json`。该测试直接覆盖 host helper、sidecar、真实本地模型和 Rust SQLite 查询；尚未启动 Tauri WebView 做桌面 UI 端到端验收。
- macOS arm64 集成包 draft.2：临时集成 Codex host `7ab8048` 与 WorkBuddy UI `cb507f7`（其前置 UI adapter 基线 `b92e859`），没有合并或提交 overlay。DMG 为 50,538,324 bytes，SHA-256 `4e14db53325f0c9f5838b4f86a3e063eb81d15cbaf30dc7eaa95f13a13ba6bea`；app ad-hoc 签名检查通过，未公证；侧车 manifest SHA-256 `ec895be625c7aa164e5640ca27ee2dcedaeb5fc8c3208083f88067aebc2d4db1`，1,187 文件通过校验。对 draft.2 的打包侧车单独运行 status/shutdown smoke：health/status 通过、Ollama ready、`cloudFallback=false`、优雅关闭通过、未打包模型权重，继承的云凭据被忽略。对 app 1,192 文件的常见凭据模式扫描为 0 命中；扫描不构成无秘密的穷尽证明。完整记录见 `backend/evidence/tauri-macos-draft2-local-agent-ui-20261026.json`。
- draft.2 的 Tauri WebView → host → sidecar → SQLite 真实 UI 流程及 `open_item` 确认回执仍未验证：Computer Use 选择已有影序 app 时返回 `timeoutReached`；机器上已有同 bundle ID 的 app 正在 WorkBuddy worktree 运行，未关闭或替换它。下一步需在不触碰该活动会话的独立桌面进程环境中完成 UI 联调。不得把 bundle smoke 说成 WebView 验收。
- 当前分支远端复核：Codex `7ab8048`、WorkBuddy `cb507f7`、Agent `1d88997`、main `9523d32`、mobile `090eec9`、Windows 验收 `22e418c`。Codex 工作树干净。SHA 为此次复核时的分支状态，之后以各分支最新 HEAD 为准。
- Windows x64 验收与移动端设备验收均由同学负责；Codex 只维护相应分支交接文档，不替代真实平台测试。
- 本机存储目录里的 `target`、`evidence`、测试材料、日志、安装包和当前使用的依赖目录都有构建/验收用途，暂不清理。只读扫描未发现可安全删除且确认无人使用的项目文件。

## 交接与暂停要求

每次交接至少记录：接收方、来源和目标分支、基线 commit、负责文件、完成/未完成项、实际验证命令与结果、证据路径、阻塞和下一步。接收方只依据该文档继续。提交时只暂存本交接任务拥有的文件，不夹带其他工作树的脏代码。

当前已按用户新指示完成本地/远端分支同步并开始开发。不得跨角色覆盖未提交改动；不得部署或发布。每个小片经验证后，只提交并推送所属分支，再更新接收方的交接文档。
