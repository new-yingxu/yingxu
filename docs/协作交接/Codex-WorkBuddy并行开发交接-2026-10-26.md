# 影序并行开发与阶段交接（2026-10-26）

> 本文是 Codex、WorkBuddy、Agent 开发会话之间的分工和开发顺序交接。任何开发人员、软件会话、分支或阶段交接，都要在接收方所在分支提交交接文档；聊天消息只通知文档路径，不代替文档。本方案交给用户审阅前，所有开发会话暂停代码修改。

## 用户确认的规则

- Codex 与 WorkBuddy 使用不同 Git 分支并行开发，互不覆盖；所有代码只提交分支，不推主线。最终验收和部署负责人由用户指派。
- 先完成桌面本地数据库、本地 Agent、本地模型和本地文件闭环，再进入云端 Agent、CloudBase 账号、数据库和跨端同步。
- 每个小片先确定 UI 状态与接口契约，再开发并立即测试；失败先修复再进入下一片。
- 移动端开发与 Windows 测试由用户的同学负责，Codex 不负责这两项实现和平台验收。
- 交接文档和对应代码、测试证据一并提交至各自分支。不得将未提交工作树描述为已交付。

## 分支与文件所有权

| 负责人 | 本地分支 | 唯一负责范围 |
|---|---|---|
| Codex 总指挥 | `codex/yingxu-desktop-agent` | 桌面 Rust/SQLite 本地内核、FTS5、备份/校验/报告/任务状态、Tauri 本地宿主、接口契约与验收统筹。主要范围：`backend/core/**`、`backend/tauri-host/**`。 |
| WorkBuddy 全栈开发 | `workbuddy/dev` | 桌面 Vue 页面、状态流、组件、前端 adapter 与 UI 测试。主要范围：`desktop-ui/src/**`、`desktop-ui/tests/**`。不改 Rust/云端服务。 |
| Agent 开发会话 | `codex/cloud-agent-store` 独立 worktree | 当前阶段负责本地 LangGraph/runtime、本地模型 provider、Agent 工具回合和与桌面宿主的契约交接。仅写自己的隔离 worktree；云端实现/部署放在本地闭环之后。 |
| 移动端同学 | `mobile/expo-v1` | Expo Android 与 iOS PWA 的移动端实现和对应设备记录。按该分支的移动端交接文档执行。 |
| Windows 测试同学 | `qa/windows-acceptance` | Windows x64 安装、启动和功能验收，只提交测试记录、环境信息、截图及复现材料。按该分支的 Windows 交接文档执行。 |

Codex 与 WorkBuddy 从共同已提交基线 `415d04f` 分开。Codex 分支不包含 WorkBuddy 脏工作树改动。Agent 分支也有未提交改动，必须保持在该 worktree，审查前不搬运、不 cherry-pick、不合并。

## 推荐开发顺序（本轮只提交方案，等用户审阅）

0. **总指挥准备**：核对基线、接口、文件所有权和交接证据；本轮结束后暂停，等用户明确继续。
1. **WorkBuddy：桌面 UI 小片**。按页面定义扫描、预检、目标确认、备份进度、成功/部分完成/失败/取消等状态；提交 UI 行为和接口字段，配套 UI 检查结果。
2. **Agent 开发：本地 Agent 小片**。先给出本地 runtime/model/工具协议与字段映射，再完成模型往返、只读查询和结构化结果；写文件操作只能形成提议，等待本机确认，不绕过 Tauri 宿主。
3. **Codex：本地数据与备份内核**。顺序为设备/目录预检 → 用户确认后复制及 SHA-256 → 本地记录和可追溯报告 → 暂停/取消/恢复/重试 → 多个本地目标结果。每一片立即做对应测试，失败先修复。
4. **三方本地集成**。逐片把 UI、Tauri、本地 DB、Agent/runtime 与本地模型接起来；在隔离临时目录与合成素材中检查读写边界、确认/拒绝、错误和恢复；完成桌面构建与启动验证。只有对应结果和交接文档齐全，才进入下一片。
5. **云端阶段**。本地闭环过关后，再安排云端 Agent、CloudBase 部署/身份/账号/数据库和同步；密钥、账号授权或部署授权按用户规则停在授权点。
6. **独立平台工作**。移动端同学按 `mobile/expo-v1` 开发和测试；Windows 同学按 `qa/windows-acceptance` 做 x64 实测。平台记录不由 macOS 调试或交叉构建替代。

## 当前状态（本次只读核查）

- 已推送主线和五条开发分支，并用 `git ls-remote --heads origin` 回读验证本地/远端分支头一致。为防止公开仓库历史暴露已删除的本机工作笔记，六条分支历史已清理并在最终文件树不变的前提下重写；`main` 为此次恢复源码快照。当前不提交未完成的工作树改动。
- WorkBuddy 的 `workbuddy/dev` 有未提交前端文件：`desktop-ui/src/App.vue`、`desktop-ui/src/api/contracts.ts`、`desktop-ui/src/api/tauri.ts`、`desktop-ui/src/components/ModelConsent.vue`，且 `desktop-ui/src/api/local-agent.ts` 未跟踪。它们保持原样，尚不能算作分支提交或 GitHub 交付。
- Agent 的 `codex/cloud-agent-store` 有大量未提交本地 Agent/云端代码、测试、迁移和 evidence。它们只留在隔离 worktree，未提交、未推送，也未并入 Codex 分支；待用户审阅后，再依新阶段顺序处理。不要据此宣称本地/云端闭环已验收。
- 对应交接文档已随上述分支提交并回读到 GitHub；各分支头见本节首项。WorkBuddy UI 与 Agent worktree 的未提交实现均没有进入这些推送提交。
- 本机已识别外接卡 `/dev/disk4`（NTFS，`SD_Card`，约 64 GB），挂载在 `/Volumes/SD_Card`；目前只确认系统挂载，不代表已完成照片读取/备份验收。Windows x64 验收与移动端设备验收均由同学负责。
- 本机存储目录里的 `target`、`evidence`、测试材料、日志、安装包和当前使用的依赖目录都有构建/验收用途，暂不清理。只读扫描未发现可安全删除且确认无人使用的项目文件。

## 交接与暂停要求

每次交接至少记录：接收方、来源和目标分支、基线 commit、负责文件、完成/未完成项、实际验证命令与结果、证据路径、阻塞和下一步。接收方只依据该文档继续。提交时只暂存本交接任务拥有的文件，不夹带其他工作树的脏代码。

当前已按用户新指示完成本地/远端分支同步，可开始开发。不得跨角色覆盖未提交改动；不得部署或发布。
