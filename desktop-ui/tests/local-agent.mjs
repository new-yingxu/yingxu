import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const moduleDir = process.env.YINGXU_UI_TEST_MODULE_DIR;
const localAgentPath = moduleDir ? pathToFileURL(resolve(moduleDir, "local-agent.js")).href : "../evidence/local-agent-module/local-agent.js";
const tauriPath = moduleDir ? pathToFileURL(resolve(moduleDir, "tauri.js")).href : "../evidence/adapter-module/tauri.js";
const modelConsentPath = moduleDir ? pathToFileURL(resolve(moduleDir, "model-consent.js")).href : "../evidence/model-consent-module/model-consent.js";
const { createLocalAgentService, localAgentStatusView, shouldShowLocalAgentAdapterNotice, withLocalAgentStatusTimeout } = await import(localAgentPath);
const { createTauriApi } = await import(tauriPath);
const { modelFailure } = await import(modelConsentPath);

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
const expiresAt = () => new Date(Date.now() + 60_000).toISOString();
const completed = (sessionId, turnId, answer = "已找到素材") => ({
  status: "completed",
  sessionId,
  turnId,
  query: "找素材",
  orchestrator: "langgraph",
  executionAuthorized: false,
  filesChanged: false,
  modelRequests: 2,
  response: {
    answer,
    matches: [{
      kind: "local_media",
      media: { mediaId: "media-1", name: "海边.jpg", kind: "photo", bytes: 2048, qualityState: "good" },
    }],
    model: modelStatus.model,
    provider: modelStatus.provider,
    executionAuthorized: false,
    filesChanged: false,
  },
});

{
  let reads = 0;
  await assert.rejects(
    withLocalAgentStatusTimeout(() => { reads += 1; return new Promise(() => {}); }, 5),
    { code: "LOCAL_AGENT_STATUS_TIMEOUT", automaticRetryAllowed: false },
  );
  assert.equal(reads, 1, "a timed-out keychain status check must not be repeated");
}

{
  const states = [
    [{ ...modelStatus }, "ready", "已就绪 · qwen2.5:7b"],
    [{ ...modelStatus, configured: false, availability: "ollama_unavailable" }, "ollama_unavailable", "Ollama 未运行"],
    [{ ...modelStatus, configured: false, availability: "model_not_installed" }, "model_not_installed", "模型未安装"],
  ];
  for (const [status, expectedState, expectedLabel] of states) {
    const view = localAgentStatusView(status);
    assert.equal(view.availability, expectedState);
    assert.equal(view.label, expectedLabel);
    assert.equal(view.ready, expectedState === "ready");
  }
  assert.equal(localAgentStatusView(null, false).ready, false);
  assert.equal(shouldShowLocalAgentAdapterNotice('native', true), false, 'runtime health is shown by model.status when the native adapter exists');
  assert.equal(shouldShowLocalAgentAdapterNotice('native', false), true, 'show the adapter notice only when a native adapter is missing');
  assert.equal(shouldShowLocalAgentAdapterNotice('mock', false), false, 'mock preview does not claim a native bridge failure');
}

{
  const base = {
    startTurn: async () => { throw new Error("not used"); },
    submitResults: async () => { throw new Error("not used"); },
  };
  const make = (catalog) => createLocalAgentService({
    transport: { ...base, status: async () => modelStatus, listLocalModels: async () => catalog },
    executeTool: async () => ({}), prepareAction: async () => { throw new Error("not used"); },
    confirmAction: async () => { throw new Error("not used"); }, confirmModelUse: async () => false,
  });
  assert.deepEqual(await make({ available: true, models: ["qwen2.5:7b", "llama3.1:8b"] }).model.listLocalModels(), {
    available: true, models: ["qwen2.5:7b", "llama3.1:8b"],
  });
  await assert.rejects(make({ available: true, models: ["https://attacker.invalid"] }).model.listLocalModels(), { code: "LOCAL_AGENT_CONTRACT_MISMATCH" });
  await assert.rejects(make({ available: false, models: ["qwen2.5:7b"] }).model.listLocalModels(), { code: "LOCAL_AGENT_CONTRACT_MISMATCH" });
}

{
  for (const unavailable of ["ollama_unavailable", "model_not_installed", "sidecar_unavailable"]) {
    let starts = 0;
    const service = createLocalAgentService({
      transport: {
        status: async () => ({ ...modelStatus, configured: false, availability: unavailable, cloudFallback: false }),
        startTurn: async () => { starts += 1; throw new Error("must not dispatch to any model"); },
        submitResults: async () => { throw new Error("must not continue"); },
      },
      executeTool: async () => { throw new Error("must not run tools"); },
      prepareAction: async () => { throw new Error("must not prepare an action"); },
      confirmAction: async () => { throw new Error("must not confirm an action"); },
      confirmModelUse: async () => true,
    });
    await assert.rejects(service.ask("找素材"), { code: "LOCAL_AGENT_UNAVAILABLE" });
    assert.equal(starts, 0, `${unavailable} must not fall back to another model`);
  }
  const invalid = createLocalAgentService({
    transport: { status: async () => ({ ...modelStatus, cloudFallback: true }), startTurn: async () => { throw new Error("must not dispatch"); }, submitResults: async () => { throw new Error("must not continue"); } },
    executeTool: async () => ({}), prepareAction: async () => { throw new Error("unexpected"); }, confirmAction: async () => { throw new Error("unexpected"); }, confirmModelUse: async () => true,
  });
  await assert.rejects(invalid.model.status(), { code: "LOCAL_AGENT_CONTRACT_MISMATCH" });
}

{
  const calls = [];
  const toolData = {
    tool: "search_media",
    scope: "current_device",
    query: "海边",
    matches: [{ mediaId: "media-1", name: "海边.jpg", kind: "photo", bytes: 2048, qualityState: "good" }],
    truncated: false,
    executionAuthorized: false,
  };
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) => {
        calls.push({ type: "start", request });
        return {
          status: "awaiting_local_tools", sessionId: request.sessionId, turnId: request.turnId,
          query: request.query, orchestrator: "langgraph", executionAuthorized: false,
          filesChanged: false, modelRequests: 1, continuationToken: "opaque-continuation",
          localToolRequests: [{
            callId: "call-1", tool: "search_media", arguments: { query: "海边", limit: 5 },
            sessionId: request.sessionId, turnId: request.turnId, scope: "current_desktop_device",
            expiresAt: expiresAt(), executionAuthorized: false, requiresLocalConfirmation: false,
          }],
        };
      },
      submitResults: async (sessionId, turnId, request) => {
        calls.push({ type: "results", sessionId, turnId, request });
        return completed(sessionId, turnId);
      },
    },
    executeTool: async (tool, args) => {
      calls.push({ type: "tool", tool, args });
      return toolData;
    },
    prepareAction: async () => { throw new Error("unexpected open plan"); },
    confirmAction: async () => { throw new Error("unexpected open confirmation"); },
    confirmModelUse: async () => true,
  });
  const reply = await service.ask("找素材");
  assert.equal(reply.orchestrator, "langgraph");
  assert.equal(reply.matches[0].kind, "local_media");
  assert.deepEqual(calls.find((c) => c.type === "results").request.results[0], {
    callId: "call-1", tool: "search_media", status: "succeeded",
    data: { truncated: false, matches: toolData.matches },
  });
  assert.equal(JSON.stringify(calls).includes("path"), false);
}

{
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) => ({
        status: "awaiting_local_tools", sessionId: request.sessionId, turnId: request.turnId,
        query: request.query, orchestrator: "langgraph", executionAuthorized: false,
        filesChanged: false, modelRequests: 1, continuationToken: "opaque-continuation",
        localToolRequests: [{
          callId: "call-uncertain", tool: "search_media", arguments: { query: "IMG_2048.png", kind: "photo", quality: "all", limit: 10 },
          sessionId: request.sessionId, turnId: request.turnId, scope: "current_desktop_device",
          expiresAt: expiresAt(), executionAuthorized: false, requiresLocalConfirmation: false,
        }],
      }),
      submitResults: async () => { throw { code: "LOCAL_AGENT_REQUEST_REJECTED", message: "do not expose transport detail" }; },
    },
    executeTool: async () => ({
      tool: "search_media", scope: "current_device", query: "IMG_2048.png", matches: [],
      truncated: false, executionAuthorized: false,
    }),
    prepareAction: async () => { throw new Error("unexpected"); },
    confirmAction: async () => { throw new Error("unexpected"); },
    confirmModelUse: async () => true,
  });
  await assert.rejects(service.ask("查找 IMG_2048.png"), (error) => {
    assert.equal(error.code, "LOCAL_AGENT_CONTINUATION_UNCONFIRMED");
    assert.equal(error.modelDispatched, true);
    assert.equal(error.diagnosticCode, "LOCAL_AGENT_REQUEST_REJECTED");
    assert.equal(error.message.includes("do not expose"), false);
    const visible = modelFailure(error);
    assert.equal(visible.code, "LOCAL_AGENT_CONTINUATION_UNCONFIRMED");
    assert.match(visible.message, /本机 Agent 工具结果或后续回答未确认返回/);
    assert.equal(visible.message.includes("LOCAL_AGENT_REQUEST_REJECTED"), false);
    assert.equal(visible.diagnosticCode, "LOCAL_AGENT_REQUEST_REJECTED");
    return true;
  });
}

{
  let starts = 0;
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async () => { starts += 1; throw new Error("must not dispatch"); },
      submitResults: async () => { throw new Error("must not continue"); },
    },
    executeTool: async () => { throw new Error("must not run tools"); },
    prepareAction: async () => { throw new Error("must not prepare an action"); },
    confirmAction: async () => { throw new Error("must not confirm an action"); },
    confirmModelUse: async () => false,
  });
  await assert.rejects(service.ask("找素材"), (error) => error.code === "USER_CANCELLED");
  assert.equal(starts, 0);
}

{
  let prompts = 0;
  let starts = 0;
  const remoteStatus = { ...modelStatus, provider: "remote-provider", region: "cloud" };
  const service = createLocalAgentService({
    transport: {
      status: async () => remoteStatus,
      startTurn: async () => { starts += 1; throw new Error("must not dispatch remotely"); },
      submitResults: async () => { throw new Error("must not continue"); },
    },
    executeTool: async () => { throw new Error("must not run tools"); },
    prepareAction: async () => { throw new Error("must not prepare an action"); },
    confirmAction: async () => { throw new Error("must not confirm an action"); },
    confirmModelUse: async () => { prompts += 1; return true; },
  });
  await assert.rejects(service.ask("找素材"), (error) => error.code === "LOCAL_PROVIDER_NOT_ALLOWED");
  assert.equal(prompts, 0);
  assert.equal(starts, 0);
}

{
  let toolCalls = 0;
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) => ({
        status: "awaiting_local_tools", sessionId: request.sessionId, turnId: request.turnId,
        query: request.query, orchestrator: "langgraph", executionAuthorized: false,
        filesChanged: false, modelRequests: 1, continuationToken: "opaque-continuation",
        localToolRequests: [{
          callId: "call-bad", tool: "search_media", arguments: { query: "海边", path: "/private/photo.jpg" },
          sessionId: request.sessionId, turnId: request.turnId, scope: "current_desktop_device",
          expiresAt: expiresAt(), executionAuthorized: false, requiresLocalConfirmation: false,
        }],
      }),
      submitResults: async () => { throw new Error("must not continue"); },
    },
    executeTool: async () => { toolCalls += 1; return {}; },
    prepareAction: async () => { throw new Error("must not prepare an action"); },
    confirmAction: async () => { throw new Error("must not confirm an action"); },
    confirmModelUse: async () => true,
  });
  await assert.rejects(service.ask("找素材"), (error) => error.code === "LOCAL_AGENT_CONTRACT_MISMATCH");
  assert.equal(toolCalls, 0);
}

{
  const calls = [];
  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) => ({
        status: "awaiting_local_tools", sessionId: request.sessionId, turnId: request.turnId,
        query: request.query, orchestrator: "langgraph", executionAuthorized: false,
        filesChanged: false, modelRequests: 1, continuationToken: "opaque-continuation",
        localToolRequests: [{
          callId: "open-call", tool: "open_item", arguments: { mediaId: "media-1" },
          sessionId: request.sessionId, turnId: request.turnId, scope: "current_desktop_device",
          expiresAt: expiresAt(), executionAuthorized: false, requiresLocalConfirmation: true,
        }],
      }),
      submitResults: async (sessionId, turnId, request) => {
        calls.push(request.results[0]);
        return completed(sessionId, turnId, "素材已按你的确认打开");
      },
    },
    executeTool: async () => { throw new Error("read tool not expected"); },
    prepareAction: async (actionId, fileId, expiresAtMs) => ({ actionId, fileId, tool: "open_item", name: "海边.jpg", expiresAtMs }),
    confirmAction: async (actionId, accepted) => {
      assert.equal(accepted, true);
      return { actionId, tool: "open_item", status: "succeeded", data: { mediaId: "media-1", opened: true, receiptId: "receipt-1" }, executionAuthorized: false };
    },
    confirmModelUse: async () => true,
  });
  const proposal = await service.ask("打开素材");
  assert.equal(proposal.proposedAction.kind, "open");
  assert.equal(calls.length, 0, "the model continuation waits for local confirmation");
  const reply = await service.confirm(proposal.proposedAction.actionId, true);
  assert.equal(reply.text, "素材已按你的确认打开");
  assert.deepEqual(calls[0], {
    callId: "open-call", tool: "open_item", status: "succeeded",
    data: { mediaId: "media-1", opened: true, receiptId: "receipt-1" },
  });
}

{
  const invalidReceipts = [
    {},
    { mediaId: "media-1", opened: false, receiptId: "receipt-2" },
    { mediaId: "media-1", opened: true },
    { mediaId: "another-media", opened: true, receiptId: "receipt-3" },
  ];
  for (const data of invalidReceipts) {
    const service = createLocalAgentService({
      transport: {
        status: async () => modelStatus,
        startTurn: async (request) => ({
          status: "awaiting_local_confirmation", sessionId: request.sessionId, turnId: request.turnId,
          query: request.query, orchestrator: "langgraph", executionAuthorized: false,
          filesChanged: false, modelRequests: 1,
          proposal: { actionId: "bad-receipt", tool: "open_item", fileIds: ["media-1"], expiresAt: expiresAt() },
          response: { answer: "等待确认", matches: [], model: modelStatus.model, provider: modelStatus.provider, executionAuthorized: false, filesChanged: false },
        }),
        submitResults: async () => { throw new Error("invalid open receipt must stop the turn"); },
      },
      executeTool: async () => ({}),
      prepareAction: async (actionId, fileId, expiresAtMs) => ({ actionId, fileId, tool: "open_item", name: "海边.jpg", expiresAtMs }),
      confirmAction: async (actionId) => ({ actionId, tool: "open_item", status: "succeeded", data, executionAuthorized: false }),
      confirmModelUse: async () => true,
    });
    const proposal = await service.ask("打开素材");
    await assert.rejects(service.confirm(proposal.proposedAction.actionId, true), { code: "OPEN_RECEIPT_INVALID" });
  }

  const service = createLocalAgentService({
    transport: {
      status: async () => modelStatus,
      startTurn: async (request) => ({
        status: "awaiting_local_confirmation", sessionId: request.sessionId, turnId: request.turnId,
        query: request.query, orchestrator: "langgraph", executionAuthorized: false,
        filesChanged: false, modelRequests: 1,
        proposal: { actionId: "declined-but-succeeded", tool: "open_item", fileIds: ["media-1"], expiresAt: expiresAt() },
        response: { answer: "等待确认", matches: [], model: modelStatus.model, provider: modelStatus.provider, executionAuthorized: false, filesChanged: false },
      }),
      submitResults: async () => { throw new Error("declined operation must not continue as success"); },
    },
    executeTool: async () => ({}),
    prepareAction: async (actionId, fileId, expiresAtMs) => ({ actionId, fileId, tool: "open_item", name: "海边.jpg", expiresAtMs }),
    confirmAction: async (actionId) => ({ actionId, tool: "open_item", status: "succeeded", data: { mediaId: "media-1", opened: true, receiptId: "receipt-4" }, executionAuthorized: false }),
    confirmModelUse: async () => true,
  });
  const proposal = await service.ask("打开素材");
  await assert.rejects(service.confirm(proposal.proposedAction.actionId, false), { code: "OPEN_RECEIPT_INVALID" });
}

{
  const ipc = [];
  const consent = [];
  const api = createTauriApi({
    confirm: async (value) => { consent.push(value); return true; },
    invoke: async (command, args = {}) => {
      ipc.push({ command, args });
      if (command === "agent_local_status") return modelStatus;
      if (command === "agent_local_turn") {
        const request = args.request;
        return {
          status: "awaiting_local_confirmation", sessionId: request.sessionId, turnId: request.turnId,
          query: request.query, orchestrator: "langgraph", executionAuthorized: false,
          filesChanged: false, modelRequests: 1,
          proposal: { actionId: "action-1", tool: "open_item", fileIds: ["media-1"], expiresAt: expiresAt() },
          response: { answer: "找到素材，等待确认", matches: [], model: modelStatus.model, provider: modelStatus.provider, executionAuthorized: false, filesChanged: false },
        };
      }
      if (command === "agent_prepare_action") return {
        actionId: args.actionId, fileId: args.fileId, tool: "open_item", name: "海边.jpg", expiresAtMs: Date.now() + 60_000,
      };
      if (command === "agent_confirm_action") return {
        actionId: args.actionId, tool: "open_item", status: "succeeded",
        data: { mediaId: "media-1", opened: true, receiptId: "receipt-1" }, executionAuthorized: false,
      };
      throw new Error(`unexpected IPC ${command}`);
    },
  });
  const proposal = await api.agent.ask("打开素材");
  assert.equal(proposal.proposedAction.actionId, "action-1");
  assert.equal(consent.length, 1);
  assert.deepEqual(ipc.map((entry) => entry.command), ["agent_local_status", "agent_local_status", "agent_local_turn", "agent_prepare_action"]);
  assert.deepEqual(ipc[2].args.request.capabilities, ["search_media", "get_metadata", "get_backup_job", "open_item"]);
  assert.equal(JSON.stringify(ipc).includes("bearer"), false);
  const result = await api.agent.confirm("action-1", true);
  assert.equal(result, "已通过本机默认应用打开 海边.jpg。");
  assert.equal(ipc.at(-1).command, "agent_confirm_action");
  assert.equal(ipc.at(-1).args.accepted, true);
}

console.log(JSON.stringify({ passed: 10, checks: ["local status tri-state presentation", "unavailable local states never fall back", "read tool continuation", "continuation IPC failure stays local and uncertain", "model consent cancellation", "remote provider blocked", "path argument rejected before execution", "open waits for confirmation", "malformed open receipts are rejected", "Tauri IPC mapping"] }, null, 2));
