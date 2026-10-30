# WorkBuddy 前端任务交接（Codex 要求项）

> 接收方：WorkBuddy 侧的**新开发会话**（本文件由框架梳理会话代 Codex 总指挥转写，内容全部来自 Codex 分支提交，可逐条溯源）。
> 分支：`workbuddy/dev`；工作目录 `/Users/nanyu/Documents/AI项目/new-影序agent开发`。

## 0. 一句话背景

影序 = 摄影师用的本地影像备份与整理软件（Tauri 2 + Vue 3 + TS 前端 + Rust 本地内核）。多会话并行：Codex 总指挥管 `backend/**`，WorkBuddy 管 `desktop-ui/**`，Agent 会话管本地模型与 Agent 编排。本文件是**Codex 已交付的 Rust 能力对前端的全部要求**，请逐项实现。

## 1. 起点状态（开工前必读）

| 项 | 状态 |
|---|---|
| 本地 HEAD | `b92e859`（含 ui-slice-1：备份流程 UI 状态机 + 终态横幅），父提交 `3f80b68` = `origin/workbuddy/dev` |
| 远端 | `origin/workbuddy/dev` 仍在 `3f80b68`，**ui-slice-1 尚未推送**；是否推送由用户决定，别自行 push |
| 工作区 | **共享目录，有并行会话正在写**：`backend/core/src/{api.rs,services.rs}`、`backend/tauri-host/src/main.rs`、`backend/evidence/search-page-*` 是 Codex 总指挥的进行中改动；`desktop-ui/src/api/{mock.ts,tauri.ts}`、`desktop-ui/tests/native-adapter.mjs`、`desktop-ui/HANDOFF-ui-slice-1.md` 是上一轮未提交的 searchPage 前端适配 |
| 铁律 | **只 `git add desktop-ui/`**，绝不 `git add -A`、绝不碰 `backend/**`、绝不 push |

## 2. 任务清单

### 任务 1：分页资料库检索（对应 Rust 提交 `9fe6526`）

Rust 侧已就绪，**前端尚未对接**。

- 新方法：`library.searchPage`，入参 `{ query, kind, quality, limit, offset }`
  - `limit`：1–200，默认 200；`offset`：≤ 1,000,000。超范围 Rust 会报错，前端不要自造更宽的范围
- 返回（**Rust 端 JSON 统一 camelCase，字段名照抄，不要改成 snake_case**）：
  ```json
  { "items": [ /* MediaFile */ ], "total": 1284, "offset": 0, "limit": 200, "hasMore": true }
  ```
  注意是 `hasMore`，不是 `has_more`
- 老方法 `library.search` 仍保留（上限 500），供既有调用方使用，**不要删**
- 前端要做：
  1. `DesktopApi.library` 增加 `searchPage(query, kind, quality, limit?, offset?)`
  2. `api/tauri.ts` 走 IPC `backend_request` 的 `library.searchPage`；`api/mock.ts` 同步实现（浏览器预览用）
  3. 资料库页面接入分页：滚动/翻页加载、显示总数、末页禁用下一页
  4. 与 `docs/桌面端/本地后端-API-v1.md` 对齐——该文档由 Codex 维护，**以文档与 Rust 实际返回为准**

### 任务 2：Agent 本机打开的两步确认（对应 Rust 提交 `942981f` + `cd2bce5`）

安全要求：**Agent 触发的任何本机打开都必须经过用户确认**，模型给的路径或命令一律不接受。

- 新增两个 IPC（Rust 侧已实现）：
  | IPC | 作用 | 返回字段 |
  |---|---|---|
  | `agent_prepare_action` | 宿主把模型给的媒体 ID 解析为已索引的普通文件，校验用户授权目录，生成**预览** | `{ actionId, tool: "open_item", fileId, name, expiresAtMs }` |
  | `agent_confirm_action` | 消费一次预览：用户拒绝 → **完全不访问文件**；用户确认 → 再次校验索引指纹、路径与授权目录后才调用系统默认打开器 | 回执 `{ actionId, status, ... }`，`status` 形如 `declined` / `failed` / 成功态 |
- 约束：预览 **5 分钟内有效**；`actionId` **单次使用**；拒绝分支不得触碰文件
- 前端要做：
  1. 新建类型 `LocalOpenPlan`（对应上面的预览结构）与 `LocalActionReceipt`（对应回执）——**Codex 明确要求字段名与这两个类型对齐**
  2. adapter 暴露 `prepareAction` / `confirmAction`
  3. UI：Agent 提议打开文件时，**先弹确认卡片**显示文件名/来源/过期时间，用户明确同意后才调用确认接口；拒绝后不发起任何打开动作
  4. 确认卡片要有明确的「同意 / 拒绝」两个按钮，且默认焦点在拒绝

### 任务 3：清除绕过确认的直开路径（对应 Rust 提交 `cd2bce5`）

Rust 已删除无需二次确认的 `agent_open_item` IPC（删除 56 行）。前端必须同步：
- 搜索并移除任何直接调用 `agent_open_item` 或等价"一步打开"的代码路径
- 现有 `agent.confirm(actionId, accepted)` 之类的入口，要改成走 `prepare → 用户确认 → confirm` 两步流程
- 若发现前端还有别的地方能不经确认就打开本地文件，一并收口

### 任务 4（待接，暂不要开工）：Agent 会话的 UI/adapter 字段契约

Agent 开发会话（分支 `codex/cloud-agent-store`，交接文档 `docs/协作交接/Agent本地闭环开发交接-Codex总指挥-2026-10-26.md`）会**向 WorkBuddy 提交稳定的 UI/adapter 字段契约**（模型状态、建 turn、工具请求、待本机确认提议、确认/拒绝、结果回执、重复请求、显式恢复、失败与不确定结果）。契约提交前，不要自造字段；收到后按其文档实现。

## 3. 已有资产（不要重复做）

| 成果 | 位置 | 状态 |
|---|---|---|
| 备份流程 UI 状态机（9 态）、控制动作矩阵、单目标重试守卫、终态横幅、任务级 error 渲染、`currentFile` 仅 running 显示、向导步进守卫 | `desktop-ui/src/api/backup-state.ts`、`App.vue` | 已提交 `b92e859`，**未推远端** |
| 对应测试 8/8 + 交接文档 + 证据 | `desktop-ui/tests/backup-flow-state.mjs`、`desktop-ui/HANDOFF-ui-slice-1.md`、`desktop-ui/evidence/ui-slice-1/` | 同上 |
| 真实 adapter、local agent、格式层 | `desktop-ui/src/api/{tauri.ts,local-agent.ts,format.ts,index.ts,cloud*.ts}`、`components/ModelConsent.vue` | 上一轮提交 `3f80b68`（已推远端） |
| searchPage 前端适配 | `desktop-ui/src/api/{mock.ts,tauri.ts}`、`tests/native-adapter.mjs` | **工作区未提交**，先验证再决定是否沿用 |

## 4. 硬边界

| 规则 | 说明 |
|---|---|
| 只写 | `desktop-ui/src/**`、`desktop-ui/tests/**`、`desktop-ui/evidence/**`、`desktop-ui/HANDOFF-*.md` |
| 只读 | `backend/**`、`cloud-service/**`、`infra/**`、`contracts/**`、`site/**`、其他分支的 `desktop-ui/` |
| 禁止 | `git add -A`、提交 `backend/` 改动、push、打 tag、合并 main |
| 禁止 | 自造 Rust 没有的接口字段；把 `hasMore` 写成 `has_more`；跳过用户确认直接打开文件 |
| 安全 | Agent 提议一律不可信；确认前不得访问文件；确认时必须重新校验（Rust 已实现，前端不得缓存旧凭据绕开） |

## 5. 验收标准

每个任务独立完成并独立提交：

1. `vue-tsc -b` 与 `vite build` 通过
2. 前端测试通过（不依赖 Tauri 运行时也能跑的那部分必须有）
3. `tests/native-adapter.mjs` 若涉及 Rust 契约变化，必须真实跑 Rust CLI 端到端并把输出存 `desktop-ui/evidence/<任务名>/`
4. 交接文档写明：完成项 / 未完成项 / 验证命令与实际结果 / 证据路径 / 待确认项
5. 提交只含 `desktop-ui/`；不 push

## 6. 汇报格式

完成后回报：任务 1/2/3 各自的完成情况与验证结果 → 提交 SHA 列表 → 交接文档路径 → 未完成与待确认项。**不要自行判定"验收通过"**，验收由框架梳理会话对照本文件逐条核对。

## 7. 溯源（Codex 原始提交，均在 `origin/codex/yingxu-desktop-agent`）

| 提交 | 内容 |
|---|---|
| `9fe6526` | feat(core): add paged local media search |
| `942981f` | feat(desktop): require confirmation for agent open action |
| `cd2bce5` | fix(desktop): require consent for every agent open |
| `977c68a` | fix(core): sample video footage with embedded covers |
| `5582ab5` | test: verify local media analysis on SD card |

参考文档：`docs/桌面端/本地后端-API-v1.md`（Rust 方法表）、`docs/协作交接/Codex-WorkBuddy并行开发交接-2026-10-26.md`（分工与顺序）、`docs/协作交接/Agent本地闭环开发交接-Codex总指挥-2026-10-26.md`（Agent 侧契约来源）。
