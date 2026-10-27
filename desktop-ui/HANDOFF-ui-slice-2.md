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

- 本会话执行期间，同一工作目录有并行会话同时修改 `desktop-ui/src/api/local-agent.ts`、`src/api/contracts.ts`、`src/components/ModelConsent.vue`、`tests/local-agent.mjs`（本机 Agent 状态 `availability` / `cloudFallback` 边界、状态视图 `localAgentStatusView`、打开回执校验 `verifiedOpenReceipt`）。
- 本会话的两步确认改动与该批改动在同文件内并存，已一起通过 `vue-tsc -b` 与全部测试；`f947103` 因此**同时包含**了并行会话在 `local-agent.ts` / `contracts.ts` / `tests/local-agent.mjs` 里的未提交改动（这些改动必须与本片的 `prepareAction` / `confirmAction` 改名同批提交，否则 HEAD 不自洽）。
- `src/components/ModelConsent.vue` 属并行会话文件，**未**纳入本会话提交；本会话为让 `vue-tsc -b` 通过补的一行 `computed` 导入仍留在工作树未提交（对方已自行修复可忽略）。
- 本会话提交后工作树仍留有：`desktop-ui/HANDOFF-ui-slice-1.md`、`desktop-ui/evidence/ui-slice-1/*`（上一轮未提交的证据）、`src/components/ModelConsent.vue`（并行会话），均未夹带进本会话提交。
- 并行会话仍在持续改写 `local-agent.ts`；若其后续提交再动同一文件，请以最终 `workbuddy/dev` 头为准复核本片的 `prepareAction` / `confirmAction` 契约。

## 汇总：提交与验证一览

| 任务 | 提交 | 主要证据 |
|---|---|---|
| 任务 1 分页资料库检索 | `e907cb2` | `evidence/ui-slice-2-task1/` |
| 任务 2 两步确认 | `f947103`（+ `f758fab` 测试断言对齐回执错误码） | `evidence/ui-slice-2-task2/` |
| 任务 3 清除直开路径 | `e94d39f` | `evidence/ui-slice-2-task3/` |

最终验证（`workbuddy/dev` @ `f758fab`）：

| 测试 | 结果 | 是否依赖 Rust/Tauri |
|---|---|---|
| `vue-tsc -b` | 通过 | 否 |
| `vite build` | 通过（36 modules） | 否 |
| `tests/library-paging.mjs` | 8/8 | 否 |
| `tests/agent-open.mjs` | 6/6 | 否（SSR 渲染断言） |
| `tests/no-direct-open.mjs` | 4/4 | 否 |
| `tests/backup-flow-state.mjs` | 8/8 | 否 |
| `tests/local-agent.mjs` | 9/9 | 否（注入式 mock 宿主） |
| `tests/native-adapter.mjs` | 18/18 | **是**，真实 `backend/target/debug/yingxu-core` 端到端 |

另在 `git archive HEAD` 导出的干净副本上复跑：`vue-tsc -b` + `vite build` 通过，五个纯前端测试全部通过（证明三个提交自洽，不依赖工作树里的其它改动）。

未 push、未部署、未改 `backend/**`、未自造 Rust 没有的接口字段。任务 4（Agent 会话字段契约）按交接要求未开工，等契约文档到位。

## Codex 后续复核：本机 Agent 固定横幅误报（2026-10-26）

- 现象：隔离 Tauri UI 同屏显示“已就绪 · qwen2.5:7b”“本机 Agent 与 Ollama 可用”，旧固定横幅仍显示“Agent 服务和本地执行桥未连接”。这能证明旧横幅误报，不能证明完整 Agent 工具往返通过。
- 修改：横幅只在 native runtime 且 `api.model` adapter 缺失时显示；运行时健康状态继续由 `ModelConsent` 调用 `api.model.status()` 呈现。进入本机工作区的 `api.local` 是本地资料库/备份适配器，与 Agent 模型 adapter 分开判断；相关参数改名为 `hasLocalDataAdapter`。
- 验证：`npm run build` 通过；本地 Agent 9 项、native adapter 18 项、本机模式 10 项、备份流程 8 项、资料库分页 8 项、打开确认 6 项、Ollama 引导 5 项、禁止直开 4 项均通过。证据：`evidence/agent-bridge-status-20261026.json`。
- 未完成：新 banner 条件尚未在重建后的 Tauri app 实际观察；之前合成 Agent 查询返回工具调用文本但没有最终回答。WebView→Tauri→sidecar→SQLite→模型完整回合仍需继续验证。

## Codex 后续复核：本机 Agent 续交失败与钥匙串阻塞（2026-10-27）

- 在隔离 AppData 的 `agent.sqlite` 中确认上次 UI 失败对应一条 `local_agent_steps`：step 0 为 `awaiting_tools`，`modelRequests=1`，含一个只读 `search_media` 请求；无 step 1、无 `agent_turns`、无 `agent_actions`。续交令牌已过期，旧轮次不可恢复，未重发。
- 前端把续交 IPC 异常统一包装为 `LOCAL_AGENT_CONTINUATION_UNCONFIRMED`，保留经过格式校验的诊断码，不回显原始 IPC 错误，并提示保留轮次/禁止重发；model failure 显示器改为按安全错误码字段识别，避免跨模块 Error 实例导致错误退回 `MODEL_OUTCOME_UNCERTAIN`。
- 验证：Vue/TypeScript + Vite build 通过；local-agent 10 项、native adapter 18 项、R6 14 项通过。新增 macOS arm64 隔离 DMG `影序本机验收 20261027_0.2.0-draft.4-local-qa_aarch64.dmg`，SHA-256 `ccb3f9fd770ff183836e374ce40f3af75509649eb2761925170fe345af854552`；DMG CRC 与 ad-hoc code signature 校验通过，未做公证。
- 阻塞：新包启动后 `agent_local_status` 卡在系统钥匙串读取；采样栈停于 `SecKeychainFindGenericPassword`，sidecar 未启动，UI 一直显示“正在检查”，所以新包的 Agent 往返和新错误提示尚未实机验收。详见 `evidence/agent-bridge-followup-20261027.json`。
- 用户指示遇到钥匙串解锁要求时跳过并继续，因此本轮没有再次请求解锁，也没有重复启动挂起的 `agent_local_status` 查询。

## Codex 续跑：本地 Agent 宿主回路与 CloudBase 状态（2026-10-27）

- 在全新临时目录和 SQLite 中，以虚构文件 `IMG_2048.jpg` 显式运行 `packaged_local_agent_real_model_tool_round_trip_from_host`，真实本机 `qwen2.5:7b` 完成工具请求；Tauri 宿主启动 sidecar，Rust 从隔离 SQLite 检索合成索引，再把结果交回模型并验证幂等重放，1/1 通过。证据：`backend/evidence/agent-host-loop-20261027.json`。
- 同轮回归：UI build 通过（44 modules），local Agent + native adapter 28 项通过，Rust core 64 项通过，Tauri 宿主 7 项通过。当前确认的是宿主到 sidecar 的闭环，**不等于**打包 WebView UI 闭环；后者仍被钥匙串挂起阻断。
- CloudBase 控制台只读可见函数 `yingxu-agent-api` 正常，PostgreSQL 迁移版本此前显示为 v10；从 `codex/cloud-agent-store` 找回 v4–v16 权威迁移及云服务实现，并在当前 Codex 分支隔离验证。CLI 刷新域名解析失败，本轮没有写云端或部署。状态证据见 `cloud-service/evidence/cloudbase-2026-10-27.md`。
- 下一步：不要重放先前结果不确定的 UI 轮次；真实打包 UI 闭环仍需在 Tauri App 观察新增超时提示。云端 v4–v16 源码已同步，线上先前只读状态为 v10；本地 readiness 要求 v16，CLI 登录与身份/secret 仍未准备，未迁移/未部署。

## Codex 续跑：状态查询挂起保护（2026-10-27）

- `ModelConsent` 的本机状态查询增加 25 秒上限。超时后显示本机 Agent 检查超时和“模型请求尚未发送”提示，并禁用继续刷新，避免重复调用卡住的 macOS Keychain；不会退回云端。
- 对永不返回的 mock status 做了定向回归，确认只发起一次读取并以 `LOCAL_AGENT_STATUS_TIMEOUT` 结束；Vue/TypeScript + Vite 构建通过（44 modules），本机 Agent 测试通过，native adapter 18 项通过。证据：`evidence/agent-status-timeout-20261027.json`。
- 未重启打包 App、未重复触发真实 Keychain；真实 WebView 超时文案仍需在 Tauri App 上观察。此 UI 保护不能取消已经挂起的 Rust `spawn_blocking`，但每个 App 会话不再自动或手动重复触发该读取。

## Codex 续跑：Agent 续交契约与窄屏排版复核（2026-10-27）

- 用户授权在体验环境仅应用 v11–v16；本轮仍未操作云端。CloudBase CLI dry-run 请求 `https://iaas.cloud.tencent.com/tcb_refresh` 超时，停止同原因重试。
- 真实隔离 Tauri 界面显示本机模型就绪，但续交返回 `LOCAL_AGENT_REQUEST_REJECTED`。只读核对专用 `agent.sqlite`：该轮仅持久化 step 0 `awaiting_tools`、一次模型请求和一个只读 `search_media`；没有 step 1 或文件操作，未重发旧轮。
- 根因：桌面端 `pathlessToolData(search_media)` 将 `query` 冗余放入续交 `data`；服务端严格 DTO 的 `SearchData` 仅接受 `matches` 与 `truncated`，因此在触发第二次模型请求前以校验错误拒绝。桌面端已改为只发送路径脱敏摘要及截断标志。
- 用户界面错误提示不再显示括号中的内部 IPC 诊断码；诊断字段仍保留在结构化错误对象供开发排查。保留“不自动重发、不要新建轮次”的安全提示。
- 验证：`npm run verify:adapter`（Agent 10 项、native adapter 18 项）通过；`npm run build` 通过；后端 `LocalToolResult` 严格 DTO 接受新结构并拒绝含 `query` 的旧结构。浏览器演示窄视口 391 px 截图与 DOM 宽度检查未见横向溢出；打包 Tauri 截图的侧栏和卡片文字未出界，长提示正常换行。
- 未完成：原轮 continuation state 已过期，不能在不恢复保存令牌的情况下续接；未再次发送模型请求。本机 Agent 的真实 UI 完整问答闭环仍待新一轮逐次确认后验收。截图检查发现无需改 CSS。
