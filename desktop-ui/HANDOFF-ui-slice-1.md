# 交接：桌面 UI 小片（ui-slice-1，2026-10-26）

- 接收方：任意继续 `workbuddy/dev` 桌面前端的会话
- 交付会话：WorkBuddy 全栈开发（workbuddy-ui-dev）
- 分支：`workbuddy/dev`（只提交本分支，未 push、未打 tag）
- 基线：`3f80b68`（feat(desktop): connect local agent UI adapter，本会话开工后由并行会话提交的上阶段 Agent 适配成果）
- 本会话负责文件：`desktop-ui/src/api/backup-state.ts`（新增）、`desktop-ui/src/App.vue`、`desktop-ui/src/api/contracts.ts`、`desktop-ui/src/api/format.ts`、`desktop-ui/src/style.css`、`desktop-ui/.gitignore`、`desktop-ui/tests/backup-flow-entry.ts`、`desktop-ui/tests/backup-flow-state.mjs`、`desktop-ui/evidence/ui-slice-1/**`

## 任务

按交接文档定义："按页面定义扫描、预检、目标确认、备份进度、成功/部分完成/失败/取消等状态；提交 UI 行为和接口字段，配套 UI 检查结果。"

## 差距清单（开工时盘点）

对照状态清单 idle / scanning / preview(目标确认) / running(进度) / paused / success / partial(部分成功) / failed / cancelled：

| 状态 | 开工时状态 | 差距 |
|---|---|---|
| idle | 已覆盖（备份页空态"尚未扫描来源"） | 无 |
| scanning | 已覆盖（扫描中按钮态 + ScanSummary 跳过报告） | 无 |
| preview | 已覆盖（第 4 步确认摘要 + 原生确认框展示 plan 文件/容量/空闲空间） | 无 |
| running | 已覆盖（进度/速度/phase、订阅 + 1.2s 轮询） | 未展示 runtime.currentFile；queued 与 running 在按钮判断里散落硬编码 |
| paused | 已覆盖（"继续"按钮） | 按钮可见性硬编码在模板，无可测试的状态机 |
| success | 已覆盖（state-pill） | 无终态结果摘要/下一步指引 |
| partial | 部分覆盖（state-pill + 逐目的地 mini-state + 失败目标重试） | 无任务级结果摘要（成功/失败目标计数、可重试提示） |
| failed | 部分覆盖（逐目的地 error + 重试） | 任务级 error 字段完全未渲染 |
| cancelled | 仅 state-pill | 无"已复制文件保留、重新发起"指引 |
| 向导步进 | 4 步 stepper 可任意跳步 | 无步进守卫（未扫描可直达确认摘要） |

## 实现清单

1. **`src/api/backup-state.ts`（新增）**：备份流程 UI 状态机，纯函数、无副作用、可独立测试：
   - `BackupFlowState`：9 个 UI 状态（Rust `queued` 归入 `running`，用 phase 文案区分"排队中"）；
   - `JOB_CONTROL` / `canControl()`：任务级 pause/resume/cancel/retry 按钮矩阵，对齐 B1 `jobs.control`（retry 仅失败目标）；
   - `canRetryTarget()`：单目标重试守卫（任务 partial/failed 且目标 state === failed）；
   - `jobStateToFlow()` / `isTerminal()`；
   - `summarizeResult()`：终态结果摘要（成功/失败目标计数、headline、下一步指引、是否可重试失败目标）；非终态返回 null；
   - `canEnterStep()`：备份页 4 步向导步进守卫（未扫描不可进入后续步骤，无目标不可进入确认摘要）。
2. **`contracts.ts` / `format.ts`**：`ArchiveTask` 新增 `currentFile?`，`mapJob` 仅在 running 时映射 Rust `runtime.currentFile`（B1 已有字段，未发明接口字段）。
3. **`App.vue` 任务详情页**：
   - 暂停/继续/取消按钮改用 `canControl()`；单目标重试改用 `canRetryTarget()`（行为与原硬编码等价，判断集中化）；
   - 新增任务级错误提示（`selectedTask.error`，role=alert）；
   - 新增终态结果摘要横幅 `summarizeResult`（success/partial/failed/cancelled 四色，含下一步指引）；
   - running 时显示"正在写入：{currentFile}"；
   - 备份页 stepper 按钮加 `canEnterStep` 守卫。
4. **`style.css`**：新增 `.result-banner`（四态配色，与 state-pill 同色系）与 `.current-file` 样式。
5. **测试**：`tests/backup-flow-entry.ts`（SSR 打包入口）+ `tests/backup-flow-state.mjs`（8 组断言：状态映射、控制矩阵、终态判定、partial 逐目标渲染与失败原因、单目标重试守卫、结果摘要、currentFile/speed 仅 running、向导步进守卫）。纯前端，node 直接运行，不需要 Tauri/Rust。
6. **`.gitignore`**：原 `/evidence/` 整目录忽略会挡住交接要求的证据入库，改为 `/evidence/*` 并反选 `/evidence/ui-slice-1/`（同时显式忽略 ui-slice-module/adapter-module/adapter-fixture-*）。这是对"git add 仅限清单"的一处必要偏差，见"待确认"。

## 验证（实际命令与结果）

在 `desktop-ui/` 下执行（本机无 pnpm，直接用 node_modules/.bin 等价执行 `pnpm build`）：

| 命令 | 结果 | 证据 |
|---|---|---|
| `./node_modules/.bin/vue-tsc -b && ./node_modules/.bin/vite build` | 通过，35 modules，dist 200.25 kB JS | `evidence/ui-slice-1/build.log` |
| `./node_modules/.bin/vite build --ssr tests/backup-flow-entry.ts --outDir evidence/ui-slice-module` | 通过，5.15 kB | `evidence/ui-slice-1/ssr-build.log` |
| `node tests/backup-flow-state.mjs` | 8/8 通过，无 Tauri/Rust 依赖 | `evidence/ui-slice-1/backup-flow-state-run.log`、`backup-flow-state-result.json` |
| `./node_modules/.bin/vite build --ssr src/api/tauri.ts --outDir evidence/adapter-module && node tests/local-agent.mjs` | 6/6 通过（上阶段成果回归） | 会话记录 |
| `node tests/native-adapter.mjs`（真实 Rust CLI 端到端） | 17/17 通过，含 queue pause/resume、真实备份 SHA-256、逐目标报告 | `evidence/ui-slice-1/native-adapter-run.log` |

## 未完成项

- 备份页 scanning 状态只有按钮文案，无进度/骨架动画（后端 scanReport 是一次性返回，无进度事件可接）。
- cancelled 后"重新发起任务"目前靠文案指引，未做一键重填表单（Rust 无任务级 retry，需要新任务）。
- 移动端/Windows 呈现不在本片范围。

## 阻塞与待确认

1. **`.gitignore` 修改**：为使 `evidence/ui-slice-1/**` 可入库，调整了忽略规则（见上）。如规则维护人不同意，请回退该文件并另行安排证据保存方式。
2. **仓库根出现两个空目录 `evidence/` 与 `evidence/ui-slice-1/`**：本会话因 shell 工作目录重置误建，内容已清空，但 `rmdir` 持续报 `Operation not permitted`（疑似被并行进程占用）。空目录无害，请下个会话顺手删除。
3. 会话进行期间，并行会话曾把上阶段 Agent 适配成果提交为 `3f80b68`（package.json、tauri.ts、local-agent.ts、ModelConsent.vue、tests/local-agent.mjs、tests/native-adapter.mjs）。本会话未重复提交这些文件；本片改动已基于该提交重新验证。
4. `pnpm` 不在本机 PATH，证据中的构建命令为 `node_modules/.bin` 等价调用；如需统一，可后续给 desktop-ui 提供 corepack/pnpm 环境。

## 下一步

- 三方本地集成时，用真实 Tauri 宿主验证 `local-job-updated` 事件驱动下的终态横幅与 currentFile 呈现（本片仅在 Rust CLI 端到端 + 纯前端单测验证）。
- partial/failed 的单目标重试在 UI 已接 `api.history.retry`；集成时确认 Rust `jobs.control retry` 的 targetId 语义一致。
