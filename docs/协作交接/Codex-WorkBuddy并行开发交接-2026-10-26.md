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
- 本次实卡测试还修正了验收脚本：改用 `library.analyze` 的类型分派路径，并按文件扩展名分批搜索，避免绕过 RAW 处理器及被单次 500 条结果上限截断。质量分类准确率尚未建立（没有人工标注集）；视频尚未纳入这轮媒体分析；这不是 Windows、移动端或发行验收。
- 为支持超过 500 条的本地媒体库，Codex 新增 RPC `library.searchPage`：请求 `query, kind, quality`，可选 `limit`（默认 200，允许 1–200）与 `offset`（默认 0）；响应 `{items,total,offset,limit,hasMore}`。旧 `library.search` 保留兼容，仍最多返回 500 条。WorkBuddy 后续接入媒体列表时应使用分页接口，并在条件变化时将 offset 重置为 0；不得在前端把 500 条旧接口结果当作完整列表。
- 分页小片验证：501 条合成媒体分三页（200/200/101）无遗漏、无重复；RPC 分页路由通过，201 条页大小拒绝。`cargo test -p yingxu-core` 64 项通过；`cargo build -p yingxu-core --release` 成功（Mac arm64，`SDKROOT=MacOSX26.5.sdk`）。WorkBuddy 分支的 UI 分页适配和 Windows 实测尚未完成。
- Windows x64 验收与移动端设备验收均由同学负责；Codex 只维护相应分支交接文档，不替代真实平台测试。
- 本机存储目录里的 `target`、`evidence`、测试材料、日志、安装包和当前使用的依赖目录都有构建/验收用途，暂不清理。只读扫描未发现可安全删除且确认无人使用的项目文件。

## 交接与暂停要求

每次交接至少记录：接收方、来源和目标分支、基线 commit、负责文件、完成/未完成项、实际验证命令与结果、证据路径、阻塞和下一步。接收方只依据该文档继续。提交时只暂存本交接任务拥有的文件，不夹带其他工作树的脏代码。

当前已按用户新指示完成本地/远端分支同步并开始开发。不得跨角色覆盖未提交改动；不得部署或发布。每个小片经验证后，只提交并推送所属分支，再更新接收方的交接文档。
