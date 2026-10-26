// ui-slice-1：备份流程 UI 状态机与渲染映射测试。
// 纯前端测试：vite --ssr 打包 tests/backup-flow-entry.ts 后用 node 运行，
// 不需要 Tauri 环境或 Rust CLI。
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  JOB_CONTROL,
  canControl,
  canRetryTarget,
  jobStateToFlow,
  isTerminal,
  summarizeResult,
  canEnterStep,
  mapJob,
} from "../evidence/ui-slice-module/backup-flow-entry.js";

const ui = fileURLToPath(new URL("..", import.meta.url));
const evidenceDir = path.join(ui, "evidence", "ui-slice-1");
const results = [];

// 1) job state → UI 状态映射（queued 归入 running，phase 文案区分排队）
assert.deepEqual(jobStateToFlow("queued"), "running");
assert.deepEqual(jobStateToFlow("running"), "running");
assert.deepEqual(jobStateToFlow("paused"), "paused");
assert.deepEqual(jobStateToFlow("success"), "success");
assert.deepEqual(jobStateToFlow("partial"), "partial");
assert.deepEqual(jobStateToFlow("cancelled"), "cancelled");
assert.deepEqual(jobStateToFlow("failed"), "failed");
results.push("job state maps to the 9-state UI flow");

// 2) 任务级控制矩阵（对齐 B1 jobs.control：pause|resume|cancel|retry）
for (const [state, matrix] of Object.entries(JOB_CONTROL)) {
  assert.equal(typeof matrix.pause, "boolean", state);
}
assert.equal(canControl("queued", "pause"), true);
assert.equal(canControl("running", "pause"), true);
assert.equal(canControl("running", "cancel"), true);
assert.equal(canControl("paused", "resume"), true);
assert.equal(canControl("paused", "cancel"), true);
assert.equal(canControl("paused", "pause"), false);
assert.equal(canControl("success", "cancel"), false);
assert.equal(canControl("success", "resume"), false);
assert.equal(canControl("cancelled", "retry"), false);
assert.equal(canControl("partial", "retry"), true);
assert.equal(canControl("failed", "retry"), true);
results.push("control matrix allows pause/resume/cancel only on active states");

// 3) 终态判定
assert.equal(isTerminal("running"), false);
assert.equal(isTerminal("paused"), false);
for (const state of ["success", "partial", "failed", "cancelled"])
  assert.equal(isTerminal(state), true);
results.push("terminal states are success/partial/failed/cancelled");

// 4) 部分成功渲染：mapJob 映射逐目标结果与失败原因
const partialJob = {
  id: "job-partial",
  name: "导入",
  state: "partial",
  kind: "backup",
  verify: true,
  createdAt: "2026-10-26T10:00:00Z",
  files: 10,
  totalBytes: 2048,
  progress: 0.6,
  phase: "partial",
  source: "/src/card",
  targets: [
    { id: "t1", path: "/dest/a", state: "success", copied: 8, skipped: 0, verified: 8, failed: 0 },
    { id: "t2", path: "/dest/b", state: "failed", copied: 2, skipped: 0, verified: 0, failed: 8 },
  ],
  items: [{ targetId: "t2", state: "failed", error: "DISK_FULL: 目的地空间不足" }],
  error: null,
  runtime: { speedBytesPerSecond: 0, currentFile: null },
};
const partialTask = mapJob(partialJob);
assert.equal(partialTask.state, "partial");
assert.equal(partialTask.destinations[0].state, "success");
assert.equal(partialTask.destinations[1].error, "DISK_FULL: 目的地空间不足");
// 目标进度按已处理（复制+跳过+失败）/计划文件数计算，与 Rust 逐目标统计一致
assert.equal(partialTask.destinations[0].progress, 80);
assert.equal(partialTask.destinations[1].progress, 100);
results.push("partial job renders per-destination results and failure reason");

// 5) 单目标重试守卫：仅 partial/failed 任务的 failed 目标可重试
assert.equal(canRetryTarget(partialTask, "t2"), true);
assert.equal(canRetryTarget(partialTask, "t1"), false);
const successTask = mapJob({ ...partialJob, state: "success", phase: "success" });
assert.equal(canRetryTarget(successTask, "t1"), false);
results.push("per-target retry guarded by task state and target state");

// 6) 终态结果摘要；非终态为 null
const partialSummary = summarizeResult(partialTask);
assert.ok(partialSummary);
assert.equal(partialSummary.state, "partial");
assert.equal(partialSummary.succeededTargets, 1);
assert.equal(partialSummary.failedTargets, 1);
assert.equal(partialSummary.canRetryFailedTargets, true);
assert.ok(partialSummary.headline.includes("1/2"));
assert.equal(summarizeResult(mapJob({ ...partialJob, state: "running", phase: "copying" })), null);
const cancelledSummary = summarizeResult(
  mapJob({ ...partialJob, state: "cancelled", phase: "user_cancel" }),
);
assert.equal(cancelledSummary.canRetryFailedTargets, false);
assert.ok(cancelledSummary.headline.includes("已取消"));
results.push("terminal result summary with retry hints; running has none");

// 7) running 时映射 runtime.currentFile，非 running 不显示
const runningJob = {
  ...partialJob,
  state: "running",
  phase: "copying",
  progress: 0.3,
  runtime: { speedBytesPerSecond: 1048576, currentFile: "DCIM/100/IMG_0001.CR3" },
};
const runningTask = mapJob(runningJob);
assert.equal(runningTask.currentFile, "DCIM/100/IMG_0001.CR3");
assert.match(runningTask.speed, /\/s$/);
assert.equal(partialTask.currentFile, undefined);
results.push("runtime currentFile and speed only while running");

// 8) 备份页向导步进守卫
const ctx = { scanned: false, targets: 0, busy: false };
assert.equal(canEnterStep(1, ctx), true);
assert.equal(canEnterStep(2, ctx), false);
assert.equal(canEnterStep(4, ctx), false);
assert.equal(canEnterStep(2, { ...ctx, busy: true }), false);
assert.equal(canEnterStep(3, { ...ctx, scanned: true }), true);
assert.equal(canEnterStep(4, { ...ctx, scanned: true, targets: 0 }), false);
assert.equal(canEnterStep(4, { ...ctx, scanned: true, targets: 1 }), true);
results.push("wizard step guard requires scan then destination");

await mkdir(evidenceDir, { recursive: true });
const report = {
  slice: "ui-slice-1",
  checks: results,
  passed: results.length,
  requiresTauri: false,
  requiresRustCli: false,
};
await writeFile(
  path.join(evidenceDir, "backup-flow-state-result.json"),
  JSON.stringify(report, null, 2) + "\n",
);
console.log(JSON.stringify(report, null, 2));
