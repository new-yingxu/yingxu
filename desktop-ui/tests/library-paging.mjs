// ui-slice-2 任务 1：资料库分页 UI 状态测试。
// 纯前端测试：vite --ssr 打包 tests/library-paging-entry.ts 后用 node 运行，
// 不需要 Tauri 环境或 Rust CLI。
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  LIBRARY_MAX_LIMIT,
  LIBRARY_MAX_OFFSET,
  LIBRARY_PAGE_SIZE,
  applyLibraryPage,
  canLoadNextPage,
  canLoadPreviousPage,
  createLibraryPage,
  describeLibraryPage,
  failLibraryLoad,
  libraryPageCount,
  libraryPageIndex,
  nextLibraryOffset,
  previousLibraryOffset,
  resetLibraryPage,
  startLibraryLoad,
} from "../evidence/ui-slice-2-module/library-paging-entry.js";

const ui = fileURLToPath(new URL("..", import.meta.url));
const evidenceDir = path.join(ui, "evidence", "ui-slice-2-task1");
const results = [];

const media = (id) => ({
  id,
  name: `${id}.jpg`,
  path: `/Volumes/SD_CARD/DCIM/${id}.jpg`,
  kind: "photo",
  size: "8.6 MB",
  capturedAt: "2026-10-26 10:00",
  device: "Sony A7 IV",
  location: "青岛 · 石老人",
  quality: "清晰",
  qualityState: "good",
});

// 1) 初始态：未检索、offset 0、页大小受 Rust 上限约束
const initial = createLibraryPage();
assert.equal(initial.status, "idle");
assert.equal(initial.offset, 0);
assert.equal(initial.limit, LIBRARY_PAGE_SIZE);
assert.equal(initial.items.length, 0);
assert.equal(LIBRARY_PAGE_SIZE <= LIBRARY_MAX_LIMIT, true);
results.push("initial page state is idle at offset 0");

// 2) 首页：替换列表并给出总数
let state = applyLibraryPage(startLibraryLoad(initial), {
  items: [media("a"), media("b")],
  total: 6,
  offset: 0,
  limit: 2,
  hasMore: true,
});
assert.equal(state.status, "ready");
assert.equal(state.items.length, 2);
assert.equal(state.total, 6);
assert.equal(state.hasMore, true);
results.push("first page replaces the list and keeps the total");

// 3) 下一页：追加且去重
state = applyLibraryPage(startLibraryLoad(state), {
  items: [media("c"), media("d")],
  total: 6,
  offset: 2,
  limit: 2,
  hasMore: true,
});
assert.deepEqual(state.items.map((item) => item.id), ["a", "b", "c", "d"]);
assert.equal(state.offset, 2);
results.push("next page appends and drops duplicate ids");

// 4) 末页：hasMore 为 false 时下一页禁用
state = applyLibraryPage(startLibraryLoad(state), {
  items: [media("c"), media("e")],
  total: 6,
  offset: 4,
  limit: 2,
  hasMore: false,
});
assert.deepEqual(state.items.map((item) => item.id), ["a", "b", "c", "d", "e"]);
assert.equal(canLoadNextPage(state), false, "the last page disables the next button");
assert.equal(canLoadPreviousPage(state), true);
results.push("last page disables the next-page button");

// 5) 上一页：回到 offset 0 时截断列表并禁用上一页
state = applyLibraryPage(startLibraryLoad(state), {
  items: [media("c"), media("d")],
  total: 6,
  offset: 2,
  limit: 2,
  hasMore: true,
});
assert.deepEqual(state.items.map((item) => item.id), ["a", "b", "c", "d"]);
state = applyLibraryPage(startLibraryLoad(state), {
  items: [media("a"), media("b")],
  total: 6,
  offset: 0,
  limit: 2,
  hasMore: true,
});
assert.deepEqual(state.items.map((item) => item.id), ["a", "b"]);
assert.equal(canLoadPreviousPage(state), false, "the first page disables the previous button");
results.push("going back truncates the list and disables the previous button");

// 6) 与请求矛盾的响应被拒绝，不会显示错误的总数
for (const broken of [
  { items: [media("a")], total: 3, offset: 0, limit: 2, hasMore: false },
  { items: [media("a"), media("b"), media("c")], total: 9, offset: 0, limit: 2, hasMore: false },
  { items: [media("a")], total: 3, offset: 0, limit: 0, hasMore: true },
  { items: [media("a")], total: 3, offset: LIBRARY_MAX_OFFSET + 1, limit: 2, hasMore: false },
  { items: [media("a")], total: 3, offset: 0, limit: 2, hasMore: "yes" },
  { items: [media("a"), media("b")], total: 1, offset: 0, limit: 2, hasMore: false },
]) {
  const rejected = applyLibraryPage(startLibraryLoad(initial), broken);
  assert.equal(rejected.status, "error", JSON.stringify(broken));
}
assert.equal(failLibraryLoad(initial, "读取失败").error, "读取失败");
results.push("contradictory page replies are rejected instead of shown");

// 7) 条件变化重置：回到 offset 0 并清空列表
const reset = resetLibraryPage(state);
assert.equal(reset.offset, 0);
assert.equal(reset.items.length, 0);
assert.equal(reset.status, "idle");
assert.equal(reset.total, 0);
results.push("a new search resets the paging to offset 0");

// 8) 翻页偏移与页码文案
const paged = applyLibraryPage(startLibraryLoad(initial), {
  items: [media("a")],
  total: 1284,
  offset: 60,
  limit: 60,
  hasMore: true,
});
assert.equal(nextLibraryOffset(paged), 120);
assert.equal(previousLibraryOffset(paged), 0);
assert.equal(libraryPageIndex(paged), 2);
assert.equal(libraryPageCount(paged), 22);
assert.equal(describeLibraryPage(paged), "共 1284 项 · 第 2/22 页 · 已显示 1 项");
assert.equal(describeLibraryPage(initial), "尚未检索资料库");
results.push("page offsets and the counter text follow the server total");

await mkdir(evidenceDir, { recursive: true });
await writeFile(
  path.join(evidenceDir, "library-paging-result.json"),
  JSON.stringify({ passed: results.length, checks: results }, null, 2) + "\n",
);
console.log(JSON.stringify({ passed: results.length, checks: results }, null, 2));
