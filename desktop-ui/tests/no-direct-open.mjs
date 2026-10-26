// ui-slice-2 任务 3：守卫测试 —— 前端不得存在绕过用户确认的本机打开路径。
// 纯静态检查：扫描 desktop-ui/src，不启动 Tauri / Rust。
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ui = fileURLToPath(new URL("..", import.meta.url));
const srcDir = path.join(ui, "src");
const results = [];

async function collect(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await collect(full)));
    else if (/\.(ts|vue|js|mjs)$/.test(entry.name)) files.push(full);
  }
  return files;
}

const files = await collect(srcDir);
const sources = new Map();
for (const file of files) sources.set(file, await readFile(file, "utf8"));

// 1) 宿主已删除的直开 IPC 不得在前端出现
for (const removed of ["agent_open_item", "agent_openItem"]) {
  const hits = [...sources.entries()]
    .filter(([, code]) => code.includes(removed))
    .map(([file]) => path.relative(ui, file));
  assert.deepEqual(hits, [], `${removed} must not be referenced by the frontend`);
}
results.push("the removed direct-open IPC is not referenced anywhere in src/");

// 2) 前端只允许调用白名单里的 Agent IPC
const allowed = new Set([
  "agent_local_status",
  "agent_local_turn",
  "agent_local_turn_results",
  "agent_prepare_action",
  "agent_confirm_action",
]);
const used = new Set();
for (const code of sources.values())
  for (const match of code.matchAll(/invoke<[^>]*>\(\s*"(agent_[a-z_]+)"/g))
    used.add(match[1]);
for (const match of [...sources.values()].flatMap((code) =>
  [...code.matchAll(/invoke\(\s*"(agent_[a-z_]+)"/g)].map((m) => m[1]),
))
  used.add(match);
const unexpected = [...used].filter((name) => !allowed.has(name));
assert.deepEqual(unexpected, [], "only the agreed agent IPC commands may be called");
assert.equal(used.has("agent_prepare_action"), true, "the preview IPC is used");
assert.equal(used.has("agent_confirm_action"), true, "the confirmation IPC is used");
results.push("only the agreed agent IPC commands are invoked");

// 3) 打开动作必须经过确认界面：卡片组件存在，且对话页接了它
const card = path.join(srcDir, "components", "AgentActionCard.vue");
assert.equal(sources.has(card), true, "the confirmation card component must exist");
const app = sources.get(path.join(srcDir, "App.vue")) || "";
assert.equal(app.includes("AgentActionCard"), true, "the agent page mounts the card");
assert.equal(app.includes("pendingAgentAction"), true, "the page tracks the pending action");
assert.equal(
  app.includes("confirmAgent(pendingAgentAction"),
  true,
  "the card decision goes through the confirmation flow",
);
const cardCode = sources.get(card);
assert.equal(cardCode.includes("拒绝"), true, "the card offers a decline button");
assert.equal(cardCode.includes("同意"), true, "the card offers an accept button");
assert.equal(
  cardCode.includes("decline.value?.focus()"),
  true,
  "the decline button takes the default focus",
);
results.push("the open action is gated by the confirmation card");

// 4) 确认接口只接受显式的 accepted 布尔值
const adapter = sources.get(path.join(srcDir, "api", "tauri.ts")) || "";
assert.equal(
  /agent_confirm_action",\s*\{\s*actionId,\s*accepted\s*\}/.test(adapter),
  true,
  "the confirmation IPC carries the explicit accepted flag",
);
assert.equal(
  adapter.includes("agent_prepare_action"),
  true,
  "the preview IPC is invoked before any confirmation",
);
results.push("the host confirmation IPC is called with an explicit accepted flag");

console.log(JSON.stringify({ passed: results.length, checks: results }, null, 2));
