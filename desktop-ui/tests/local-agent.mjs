import assert from "node:assert/strict";
import { createLocalAgentService } from "../evidence/local-agent-module/local-agent.js";
import { createTauriApi } from "../evidence/adapter-module/tauri.js";

const modelStatus = {
  configured: true,
  provider: "ollama",
  model: "qwen2.5:7b",
  region: "local",
  requiresExplicitConsent: true,
  executionAuthorized: false,
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
    prepareOpen: async () => { throw new Error("unexpected open plan"); },
    confirmOpen: async () => { throw new Error("unexpected open confirmation"); },
    confirmModelUse: async () => true,
  });
  const reply = await service.ask("找素材");
  assert.equal(reply.orchestrator, "langgraph");
  assert.equal(reply.matches[0].kind, "local_media");
  assert.deepEqual(calls.find((c) => c.type === "results").request.results[0], {
    callId: "call-1", tool: "search_media", status: "succeeded",
    data: { query: "海边", truncated: false, matches: toolData.matches },
  });
  assert.equal(JSON.stringify(calls).includes("path"), false);
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
    prepareOpen: async () => { throw new Error("must not prepare an action"); },
    confirmOpen: async () => { throw new Error("must not confirm an action"); },
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
    prepareOpen: async () => { throw new Error("must not prepare an action"); },
    confirmOpen: async () => { throw new Error("must not confirm an action"); },
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
    prepareOpen: async () => { throw new Error("must not prepare an action"); },
    confirmOpen: async () => { throw new Error("must not confirm an action"); },
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
    prepareOpen: async (actionId, fileId) => ({ actionId, fileId, tool: "open_item", name: "海边.jpg", expiresAt: expiresAt() }),
    confirmOpen: async (actionId, accepted) => {
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

console.log(JSON.stringify({ passed: 6, checks: ["read tool continuation", "model consent cancellation", "remote provider blocked", "path argument rejected before execution", "open waits for confirmation", "Tauri IPC mapping"] }, null, 2));
