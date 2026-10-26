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
