// ui-slice-2 任务 2：Agent 本机打开两步确认测试。
// 纯前端测试：vite --ssr 打包 tests/agent-open-entry.ts 后用 node 运行，
// 不需要 Tauri 环境或 Rust CLI。
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import {
  ACTION_WINDOW_MS,
  AgentActionCard,
  OPEN_PROPOSAL_SOURCE,
  createLocalAgentService,
} from "../evidence/ui-slice-2-module/agent-open-entry.js";
import { createTauriApi } from "../evidence/adapter-module/tauri.js";

const ui = fileURLToPath(new URL("..", import.meta.url));
const evidenceDir = path.join(ui, "evidence", "ui-slice-2-task2");
const results = [];

const modelStatus = {
  configured: true,
  provider: "ollama",
  model: "qwen2.5:7b",
  region: "local",
  requiresExplicitConsent: true,
  executionAuthorized: false,
  availability: "ready",
  cloudFallback: false,
};
const iso = (ms) => new Date(ms).toISOString();
const proposalTurn = (request, expiresAt) => ({
  status: "awaiting_local_confirmation",
  sessionId: request.sessionId,
  turnId: request.turnId,
  query: request.query,
  orchestrator: "langgraph",
  executionAuthorized: false,
  filesChanged: false,
  modelRequests: 1,
  proposal: { actionId: "action-1", tool: "open_item", fileIds: ["media-1"], expiresAt },
  response: {
    answer: "找到素材，等待确认",
    matches: [],
    model: modelStatus.model,
    provider: modelStatus.provider,
    executionAuthorized: false,
    filesChanged: false,
  },
});

// 1) 拒绝：先 prepare，用户拒绝后才 confirm，且全程没有任何"打开"调用
{
  const ipc = [];
  const api = createTauriApi({
    confirm: async () => true,
    invoke: async (command, args = {}) => {
      ipc.push({ command, args });
      if (command === "agent_local_status") return modelStatus;
      if (command === "agent_local_turn")
        return proposalTurn(args.request, iso(Date.now() + 60_000));
      if (command === "agent_prepare_action")
        return {
          actionId: args.actionId,
          tool: "open_item",
          fileId: args.fileId,
          name: "海边.jpg",
          expiresAtMs: args.expiresAtMs,
        };
      if (command === "agent_confirm_action")
        return {
          actionId: args.actionId,
          tool: "open_item",
          status: args.accepted ? "succeeded" : "declined",
          data: args.accepted
            ? { mediaId: "media-1", opened: true, receiptId: "receipt-1" }
            : {},
          executionAuthorized: false,
        };
      throw new Error(`unexpected IPC ${command}`);
    },
  });
  const proposal = await api.agent.ask("打开素材");
  assert.equal(proposal.proposedAction.kind, "open");
  assert.equal(proposal.proposedAction.source, OPEN_PROPOSAL_SOURCE);
  assert.deepEqual(
    ipc.map((entry) => entry.command),
    ["agent_local_status", "agent_local_status", "agent_local_turn", "agent_prepare_action"],
    "the model turn must not open anything before the user decides",
  );
  assert.equal(ipc.at(-1).args.expiresAtMs > Date.now(), true);
  assert.equal(ipc.at(-1).args.tool, "open_item");
  const declined = await api.agent.confirm(proposal.proposedAction.actionId, false);
  assert.equal(declined, "已取消本机操作，素材未打开。");
  assert.equal(ipc.at(-1).command, "agent_confirm_action");
  assert.equal(ipc.at(-1).args.accepted, false);
  assert.equal(
    ipc.some((entry) => entry.command.includes("open")),
    false,
    "no direct open IPC exists",
  );
  results.push("decline runs prepare then confirm(false) and never opens");
}

// 2) 同意：确认后才调用 confirm(true)，回执字段对齐宿主
{
  const ipc = [];
  const api = createTauriApi({
    confirm: async () => true,
    invoke: async (command, args = {}) => {
      ipc.push({ command, args });
      if (command === "agent_local_status") return modelStatus;
      if (command === "agent_local_turn")
        return proposalTurn(args.request, iso(Date.now() + 60_000));
      if (command === "agent_prepare_action")
        return {
          actionId: args.actionId,
          tool: "open_item",
          fileId: args.fileId,
          name: "海边.jpg",
          expiresAtMs: args.expiresAtMs,
        };
      if (command === "agent_confirm_action")
        return {
          actionId: args.actionId,
          tool: "open_item",
          status: "succeeded",
          data: { mediaId: "media-1", opened: true, receiptId: "receipt-1" },
          executionAuthorized: false,
        };
      throw new Error(`unexpected IPC ${command}`);
    },
  });
  const proposal = await api.agent.ask("打开素材");
  const opened = await api.agent.confirm(proposal.proposedAction.actionId, true);
  assert.equal(opened, "已通过本机默认应用打开 海边.jpg。");
  assert.equal(ipc.at(-1).command, "agent_confirm_action");
  assert.equal(ipc.at(-1).args.accepted, true);
  results.push("accept calls confirm(true) only after the preview");
}

// 3) 有效期窗口与宿主一致：超过 5 分钟的提议在调用宿主前就被拒绝
{
  let prepared = 0;
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) =>
        proposalTurn(request, iso(Date.now() + ACTION_WINDOW_MS + 60_000)),
      submitResults: async () => {
        throw new Error("must not continue");
      },
    },
    executeTool: async () => {
      throw new Error("must not run tools");
    },
    prepareAction: async () => {
      prepared += 1;
      throw new Error("must not prepare an action");
    },
    confirmAction: async () => {
      throw new Error("must not confirm an action");
    },
    confirmModelUse: async () => true,
  });
  await assert.rejects(
    service.ask("打开素材"),
    (error) => error.code === "ACTION_EXPIRED",
  );
  assert.equal(prepared, 0, "an out-of-window proposal never reaches the host");
  results.push("proposals outside the 5 minute window fail before the host call");
}

// 4) 宿主预览与请求不一致时拒绝（actionId / fileId / 有效期 / 多余字段）
for (const [label, plan, code] of [
  [
    "action id",
    { actionId: "other", tool: "open_item", fileId: "media-1", name: "海边.jpg", expiresAtMs: 0 },
    "LOCAL_AGENT_CONTRACT_MISMATCH",
  ],
  [
    "extra field",
    {
      actionId: "action-1",
      tool: "open_item",
      fileId: "media-1",
      name: "海边.jpg",
      expiresAtMs: 0,
      path: "/private/photo.jpg",
    },
    "LOCAL_AGENT_CONTRACT_MISMATCH",
  ],
[
    "expiry outside the window",
    {
      actionId: "action-1",
      tool: "open_item",
      fileId: "media-1",
      name: "海边.jpg",
      expiresAtMs: Date.now() + 10 * 60 * 1000,
    },
    "LOCAL_AGENT_CONTRACT_MISMATCH",
  ],
]) {
  let prepared = 0;
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) =>
        proposalTurn(request, iso(Date.now() + 60_000)),
      submitResults: async () => {
        throw new Error("must not continue");
      },
    },
    executeTool: async () => {
      throw new Error("must not run tools");
    },
    prepareAction: async (actionId, fileId, expiresAtMs) => {
      prepared += 1;
      return { ...plan, expiresAtMs: plan.expiresAtMs || expiresAtMs };
    },
    confirmAction: async () => {
      throw new Error("must not confirm an action");
    },
    confirmModelUse: async () => true,
  });
  await assert.rejects(
    service.ask("打开素材"),
    (error) => error.code === code,
    label,
  );
  assert.equal(prepared, 1, label);
}
results.push("a preview that contradicts the request is rejected");

// 5) 回执必须自证未授权执行，且失败回执不得带结果数据
{
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) =>
        proposalTurn(request, iso(Date.now() + 60_000)),
      submitResults: async () => {
        throw new Error("must not continue");
      },
    },
    executeTool: async () => {
      throw new Error("must not run tools");
    },
    prepareAction: async (actionId, fileId, expiresAtMs) => ({
      actionId,
      fileId,
      tool: "open_item",
      name: "海边.jpg",
      expiresAtMs,
    }),
    confirmAction: async (actionId) => ({
      actionId,
      tool: "open_item",
      status: "succeeded",
      data: { mediaId: "media-1", opened: true },
      executionAuthorized: true,
    }),
    confirmModelUse: async () => true,
  });
  const proposal = await service.ask("打开素材");
  await assert.rejects(
    service.confirm(proposal.proposedAction.actionId, true),
    (error) => error.code === "LOCAL_AGENT_CONTRACT_MISMATCH",
  );
}
results.push("a receipt that claims execution authorization is rejected");

// 6) 确认卡片：显示文件名 / 来源 / 过期时间，同意与拒绝两个按钮，默认焦点在拒绝
{
  const html = await renderToString(
    createSSRApp(AgentActionCard, {
      action: {
        actionId: "action-1",
        tool: "open_item",
        fileIds: ["media-1"],
        expiresAt: iso(Date.now() + 120_000),
        source: OPEN_PROPOSAL_SOURCE,
        kind: "open",
        label: "海边.jpg",
        target: "本机默认应用",
        objects: ["海边.jpg"],
      },
    }),
  );
  assert.equal(html.includes("海边.jpg"), true, "the file name is shown");
  assert.equal(html.includes(OPEN_PROPOSAL_SOURCE), true, "the proposal source is shown");
  assert.equal(/过期时间/.test(html), true, "the expiry time is shown");
  const decline = html.match(/<button[^>]*>\s*拒绝\s*<\/button>/);
  const accept = html.match(/<button[^>]*>\s*同意\s*<\/button>/);
  assert.ok(decline, "the decline button exists");
  assert.ok(accept, "the accept button exists");
  assert.equal(
    html.indexOf(decline[0]) < html.indexOf(accept[0]),
    true,
    "the decline button comes first in the dialog",
  );
  assert.equal(
    decline[0].includes("autofocus"),
    true,
    "the decline button holds the default focus",
  );
  assert.equal(
    accept[0].includes("autofocus"),
    false,
    "the accept button never holds the default focus",
  );
}
results.push("confirmation card shows file, source and expiry with 拒绝 focused by default");

await mkdir(evidenceDir, { recursive: true });
await writeFile(
  path.join(evidenceDir, "agent-open-result.json"),
  JSON.stringify({ passed: results.length, checks: results }, null, 2) + "\n",
);
console.log(JSON.stringify({ passed: results.length, checks: results }, null, 2));
