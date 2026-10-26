# 交接：桌面 UI 小片（ui-slice-2，Codex 前端要求项，2026-10-26）

- 接收方：框架梳理会话（对照 `docs/协作交接/WorkBuddy前端任务交接-Codex要求-2026-10-26.md` 逐条核对）
- 交付会话：WorkBuddy 全栈开发（workbuddy-ui-dev）
- 分支：`workbuddy/dev`（只提交本分支，未 push、未打 tag）
- 基线：`00d0c17`（框架梳理会话转写的 Codex 前端要求清单）
- 本会话负责文件：`desktop-ui/src/**`、`desktop-ui/tests/**`、`desktop-ui/evidence/**`、`desktop-ui/HANDOFF-ui-slice-2.md`
- 硬边界遵守：只 `git add desktop-ui/`；未改 `backend/**`；未自造 Rust 没有的字段；未跳过用户确认。

---

## 任务 1：分页资料库检索（对应 Rust `9fe6526`）

### 完成项

| 要求 | 落地位置 |
|---|---|
| `DesktopApi.library.searchPage(query, kind, quality, limit?, offset?)` | `src/api/contracts.ts`（类型 `MediaPage`，字段 `items/total/offset/limit/hasMore`） |
| `api/tauri.ts` 走 IPC `backend_request` 的 `library.searchPage` | `src/api/tauri.ts` `searchMediaPage()`：透传 camelCase 参数，不改 `hasMore` 为 `has_more` |
| `api/mock.ts` 同步实现（浏览器预览） | `src/api/mock.ts` `library.searchPage()`，返回同样的 `hasMore` 语义 |
| 资料库页面接入分页：翻页加载、显示总数、末页禁用下一页 | `src/App.vue` 资料库页：新增 `libraryPage` 状态、`describeLibraryPage()` 计数文案、上一页/下一页按钮 |
| 与 `docs/桌面端/本地后端-API-v1.md` 对齐 | 按文档 `limit:1..200`、`offset ≤ 1_000_000` 校验；响应字段照抄 Rust camelCase |

新增纯前端分页状态机 `src/api/library-paging.ts`（无副作用、可单测）：

- `createLibraryPage / resetLibraryPage`：新检索条件一律回到 `offset 0`；
- `applyLibraryPage`：首页替换、后续页追加并按 id 去重、回退页截断；**响应自相矛盾时判为 error，不显示错误总数**；
- `canLoadNextPage / canLoadPreviousPage`：末页禁用下一页、首页禁用上一页；
- `libraryPageIndex / libraryPageCount / describeLibraryPage`：`共 N 项 · 第 x/y 页 · 已显示 M 项`。

校验规则与 Rust `search_page` 同一不变式：`hasMore === (offset + items.length < total)`（Rust 侧 `has_more` 计算方式一致）。

### 验证（实际命令与结果）

| 命令 | 结果 | 证据 |
|---|---|---|
| `./node_modules/.bin/vue-tsc -b` | 通过（exit 0） | `evidence/ui-slice-2-task1/build.log` |
| `./node_modules/.bin/vite build` | 通过，36 modules，dist 204.59 kB JS / 40.61 kB CSS | 同上 |
| `./node_modules/.bin/vite build --ssr tests/library-paging-entry.ts --outDir evidence/ui-slice-2-module` + `node tests/library-paging.mjs` | 8/8 通过（纯前端，不依赖 Tauri/Rust） | `evidence/ui-slice-2-task1/library-paging-run.log` |
| `node tests/native-adapter.mjs` | 18/18 通过，**真实 Rust CLI**（`backend/target/debug/yingxu-core`）端到端，含新增「paged library search against the real Rust CLI」 | `evidence/ui-slice-2-task1/native-adapter-run.log` |
| `node tests/backup-flow-state.mjs` / `node tests/local-agent.mjs` | 8/8、6/6 通过（上阶段成果回归） | 会话记录 |

native-adapter 本轮新增断言：首页 `limit=2,offset=0` → `items 2 / total 3 / hasMore true`；末页 `offset=2` → `items 1 / hasMore false`；`limit=201` → 前端直接拒绝 `INVALID_PAGINATION`（Rust 也会拒绝，不自造更宽范围）。

### 未完成项

- 资料库列表当前按页追加（下一页继续追加、上一页回退截断），没有做滚动到底自动加载；交互形态待框架梳理会话确认。
- `library.search` 在 native adapter 里改为「用 `library.searchPage` 翻页取全量」，不再直接调用 Rust 旧 `library.search`（上限 500）。前端接口面保留 `search`，Rust 旧方法未删；这样既不把 500 条当完整列表，也不影响既有调用方。此改动列为待确认项。
- 本机缩略图仍只对当前已加载条目按批预取（每批 4 张、上限 24 张），分页后按需补齐，不做整库预取。

### 待确认项

1. `library.search` 改为翻页取全量是否符合 Codex「旧方法保留供既有调用方使用」的意图（Rust 侧 `library.search` 未动）。
2. 资料库分页交互采用「上一页/下一页 + 追加」而非无限滚动，是否需要改为滚动加载。

---

## 任务 2：Agent 本机打开的两步确认（对应 Rust `942981f` + `cd2bce5`）

### 完成项

| 要求 | 落地位置 |
|---|---|
| 类型 `LocalOpenPlan` 与宿主预览结构对齐 | `src/api/local-agent.ts`：`{ actionId, tool: 'open_item', fileId, name, expiresAtMs }`，与 `agent_prepare_action` 返回的 `expiresAtMs` 一致（原来用 ISO `expiresAt`，与宿主字段名不符） |
| 类型 `LocalActionReceipt` 与宿主回执对齐 | `src/api/local-agent.ts`：`{ actionId, tool, status, data, executionAuthorized }`（与 `agent_confirm_action` 一致，并做严格字段校验） |
| adapter 暴露 `prepareAction` / `confirmAction` | `src/api/tauri.ts`：`prepareAction` → IPC `agent_prepare_action`；`confirmAction` → IPC `agent_confirm_action`；`src/api/local-agent.ts` 服务选项同步改名 |
| UI 先弹确认卡片，显示文件名 / 来源 / 过期时间 | 新增 `src/components/AgentActionCard.vue`（模态卡片），`src/App.vue` 用 `pendingAgentAction` 挂载；`AgentReply.proposedAction` 新增 `source` 字段标明提议来源 |
| 明确的「同意 / 拒绝」两个按钮，默认焦点在拒绝 | 卡片内「拒绝」按钮带 `autofocus` 且 `onMounted` 主动 `focus()`；「同意」按钮无 autofocus；Esc 等同拒绝 |
| 拒绝后不发起任何打开动作 | 拒绝只走 `agent_confirm_action { accepted: false }`，宿主走 declined 分支不触碰文件；前端在 `confirm` 前不调用任何打开接口 |

安全边界的额外收紧（与宿主 `validate_action_expiry` 同规则）：

- 提议有效期必须落在「当前时间之后、5 分钟之内」，否则前端先拒（`ACTION_EXPIRED`），**不会**调用宿主；
- 宿主返回的预览必须 `actionId`/`tool`/`fileId` 与请求一致、有效期仍在 5 分钟窗口内、字段集合精确匹配，多余字段（例如路径）一律拒绝；
- 回执必须 `executionAuthorized === false`，失败/拒绝回执不得携带结果数据；
- 确认失败或过期时清空待确认动作，不在界面留下可重试的旧凭据。

### 验证（实际命令与结果）

| 命令 | 结果 | 证据 |
|---|---|---|
| `./node_modules/.bin/vue-tsc -b` | 通过（exit 0） | `evidence/ui-slice-2-task2/build.log` |
| `./node_modules/.bin/vite build` | 通过，36 modules，dist 208.92 kB JS | 同上 |
| `vite build --ssr tests/agent-open-entry.ts` + `node tests/agent-open.mjs` | 6/6 通过（纯前端，不依赖 Tauri/Rust；含 SSR 渲染断言） | `evidence/ui-slice-2-task2/agent-open-run.log` |
| `node tests/local-agent.mjs` | 9/9 通过（上阶段 + 并行会话成果回归） | `evidence/ui-slice-2-task2/local-agent-run.log` |
| `node tests/native-adapter.mjs` | 18/18 通过，真实 Rust CLI 端到端 | `evidence/ui-slice-2-task2/native-adapter-run.log` |

`tests/agent-open.mjs` 的 6 项断言：拒绝路径 prepare→confirm(false) 且全程无打开调用；同意路径确认后才 confirm(true)；超 5 分钟窗口的提议在调用宿主前失败；预览与请求矛盾（actionId 不符 / 多余字段 / 有效期超窗）一律拒绝；自称已授权执行的回执被拒；确认卡片 SSR 渲染出文件名、来源、过期时间与「拒绝 / 同意」，且默认焦点在拒绝。

### 未完成项

- 确认卡片尚未做键盘 Tab 循环锁定（ModelConsent 的对话框有，本卡片只有 Esc 与 autofocus）；可后续统一。
- 本机 Agent 打开尚未在真实 Tauri 窗口里联调（宿主 `agent_prepare_action` / `agent_confirm_action` 未在真机窗口跑过），当前验证为注入式 mock 宿主 + 真实 Rust CLI 的分页路径。
- Agent 会话侧的字段契约（任务 4）尚未收到，前端仍按现有 sidecar 字段（`proposal.expiresAt` 为 ISO 字符串）工作。

### 待确认项

1. 预览有效期前端按「宿主窗口内即接受」校验（宿主返回的 `expiresAtMs` 若与请求值不同但仍在窗口内也接受），请确认是否要改成严格相等。
2. 确认卡片默认焦点用 `autofocus` + 手动 focus 两重保险，请在真实 Tauri 窗口确认焦点确实落在「拒绝」。

---

## 任务 3：清除绕过确认的直开路径（对应 Rust `cd2bce5`）

### 完成项

- 全量核查 `desktop-ui/src/**`：前端**不存在**任何 `agent_open_item` / 一步式打开调用；宿主已删除该 IPC，前端也没有对应残留。
- 唯一可打开本机素材的路径是：`agent_prepare_action`（换预览）→ 用户在确认卡片上明确「同意」→ `agent_confirm_action { accepted: true }`（宿主此时才校验索引指纹、路径与授权目录并调用系统默认应用）。
- 新增守卫测试 `tests/no-direct-open.mjs`（静态扫描，4 项断言）：
  1. `src/` 中不出现 `agent_open_item` / `agent_openItem`；
  2. 前端只调用白名单内的 Agent IPC（`agent_local_status` / `agent_local_turn` / `agent_local_turn_results` / `agent_prepare_action` / `agent_confirm_action`）；
  3. 确认卡片组件存在且对话页确实挂载并走 `confirmAgent`；
  4. `agent_confirm_action` 必须携带显式 `accepted` 布尔值。

### 验证

| 命令 | 结果 | 证据 |
|---|---|---|
| `node tests/no-direct-open.mjs` | 4/4 通过 | `evidence/ui-slice-2-task3/no-direct-open-run.log` |

### 未完成项

- 守卫是静态扫描 + 注入式 mock 宿主级的运行时断言；真实 Tauri 窗口里的系统默认应用调用尚未实机验证（属 Windows/macOS 同学或后续集成轮次）。

---

## 与并行会话的交叉改动（需知会）

- 本会话执行期间，同一工作目录有并行会话同时修改 `desktop-ui/src/api/local-agent.ts`、`src/api/contracts.ts`、`src/components/ModelConsent.vue`、`tests/local-agent.mjs`（本机 Agent 状态 `availability` / `cloudFallback` 边界与状态视图）。
- 本会话的两步确认改动与该批改动在同文件内并存，已一起通过 `vue-tsc -b` 与全部测试。
- 为让 `vue-tsc -b` 通过，本会话在 `src/components/ModelConsent.vue` 补了一行 `computed` 导入（并行会话使用了 `computed` 但未导入）。如对方已自行修复，可回退该行。
- 本会话提交时 `desktop-ui/` 内可能仍留有并行会话的未提交改动（如 `tests/local-agent.mjs` 的可用性用例），本会话未夹带提交归属不明的内容；提交只含本片文件。

## 汇总：提交与验证一览

| 任务 | 提交 | 主要证据 |
|---|---|---|
| 任务 1 分页资料库检索 | `e907cb2` | `evidence/ui-slice-2-task1/` |
| 任务 2 两步确认 | 见本文件末尾提交记录 | `evidence/ui-slice-2-task2/` |
| 任务 3 清除直开路径 | 见本文件末尾提交记录 | `evidence/ui-slice-2-task3/` |

最终状态：`vue-tsc -b` 通过；`vite build` 通过；纯前端测试 library-paging 8/8、agent-open 6/6、no-direct-open 4/4、backup-flow-state 8/8、local-agent 9/9；真实 Rust CLI 端到端 native-adapter 18/18。未 push、未部署、未改 `backend/**`。
