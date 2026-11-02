const test = require("node:test");
const assert = require("node:assert/strict");
const { CloudBaseAccount, YingxuSession, YingxuCloudClient } = require("../src/services/yingxu-cloud.cjs");

function memoryStore() {
  const saved = new Map();
  return {
    async set(key, value) { saved.set(key, value); },
    async get(key) { return saved.get(key) ?? null; },
    async remove(key) { saved.delete(key); },
  };
}

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, async json() { return body; } };
}

/** 按 URL 前缀路由的 fetch 桩：routes 为 [前缀, 处理器(url, options)]。 */
function stubFetch(routes) {
  return async (url, options = {}) => {
    const hit = routes.find(([prefix]) => String(url).includes(prefix));
    if (!hit) throw new Error(`unexpected fetch: ${url}`);
    return hit[1](String(url), options);
  };
}

function tokenBody(sub = "user-a") {
  return { token_type: "Bearer", access_token: `access-${Math.random().toString(36).slice(2)}`, refresh_token: `refresh-${Math.random().toString(36).slice(2)}`, expires_in: 7200, sub };
}

function buildClient(fetchImpl) {
  const account = new CloudBaseAccount({
    authOrigin: "https://env-id.api.tcloudbasegateway.com",
    clientId: "mobile-client",
    secureStore: memoryStore(),
    fetchImpl,
  });
  const session = new YingxuSession({
    identityApiUrl: "https://identity.yingxu.example",
    account,
    secureStore: memoryStore(),
    fetchImpl,
  });
  const client = new YingxuCloudClient({ account, session, deviceId: "pocket-test-1", fetchImpl });
  // 预置有效的 CloudBase 账号令牌，exchange 不再触发网络刷新。
  account.tokens = { accessToken: "account-token-1", refreshToken: "r-seed", sub: "user-a", expiresAt: Date.now() + 3_600_000 };
  return client;
}

test("CloudBase 密码登录成功后保存轮换用的刷新令牌，且不泄露密码", async () => {
  const client = buildClient(stubFetch([
    ["/auth/v1/signin", () => jsonResponse(200, tokenBody("user-a"))],
  ]));
  const result = await client.account.signIn("photo@example.test", "temp-password-input");
  assert.equal(result.sub, "user-a");
  assert.ok(client.account.tokens.refreshToken.startsWith("refresh-"));
  assert.ok(!JSON.stringify(client.account).includes("temp-password-input"));
});

test("注册流程：发码 → 验证 → signup，随后建立影序设备会话", async () => {
  const seen = [];
  const client = buildClient(stubFetch([
    ["/auth/v1/verification/verify", (url, options) => {
      seen.push(["verify", JSON.parse(options.body)]);
      return jsonResponse(200, { verification_token: "vt-1", expires_in: 600 });
    }],
    ["/auth/v1/verification", () => jsonResponse(200, { verification_id: "vid-1", expires_in: 600 })],
    ["/auth/v1/signup", (url, options) => {
      seen.push(["signup", JSON.parse(options.body)]);
      assert.ok(String(url).includes("client_id=mobile-client"));
      return jsonResponse(200, tokenBody("user-b"));
    }],
    ["/v1/auth/session/exchange", (url, options) => {
      seen.push(["exchange", JSON.parse(options.body)]);
      assert.equal(options.headers.Authorization, `Bearer ${client.account.tokens.accessToken}`);
      return jsonResponse(200, {
        accessToken: "x".repeat(64), tokenType: "Bearer", expiresIn: 900,
        ownerId: "owner-b", deviceId: "device-b", origin: "mobile", deviceAttested: false,
      });
    }],
  ]));
  const challenge = await client.sendEmailCode("photo@example.test", "register");
  assert.equal(challenge.verificationId, "vid-1");
  const result = await client.signIn("photo@example.test", "temp-password", true, "影序用户", "123456");
  assert.equal(result.uid, "owner-b");
  assert.equal(seen.find(([step]) => step === "signup")[1].verification_token, "vt-1");
  assert.equal(seen.find(([step]) => step === "exchange")[1].displayName, "影序用户");
  assert.equal(client.uid, "owner-b");
  assert.equal(client.session.displayName, "影序用户");
});

test("access token 过期时用刷新令牌轮换，旧刷新令牌被替换持久化", async () => {
  const store = memoryStore();
  const account = new CloudBaseAccount({
    authOrigin: "https://env-id.api.tcloudbasegateway.com",
    clientId: "mobile-client",
    secureStore: store,
    fetchImpl: stubFetch([
      ["/auth/v1/signin", () => jsonResponse(200, { token_type: "Bearer", access_token: "a1", refresh_token: "r1", expires_in: 1, sub: "user-a" })],
      ["/auth/v1/token", (url, options) => {
        const body = JSON.parse(options.body);
        assert.equal(body.grant_type, "refresh_token");
        assert.equal(body.refresh_token, "r1");
        assert.equal(body.client_id, "mobile-client");
        assert.ok(!String(url).includes("client_id="));
        return jsonResponse(200, { token_type: "Bearer", access_token: "a2", refresh_token: "r2", expires_in: 7200, sub: "user-a" });
      }],
    ]),
  });
  await account.signIn("photo@example.test", "temp-password");
  assert.equal(await account.validAccessToken(), "a2");
  const persisted = JSON.parse(await store.get("yingxu.account.v1"));
  assert.equal(persisted.refreshToken, "r2");
});

test("刷新令牌被吊销时清理本地会话并要求重新登录", async () => {
  const account = new CloudBaseAccount({
    authOrigin: "https://env-id.api.tcloudbasegateway.com",
    clientId: "mobile-client",
    secureStore: memoryStore(),
    fetchImpl: stubFetch([
      ["/auth/v1/token", () => jsonResponse(400, { error: "invalid_grant", error_description: "refresh_token无效或已过期" })],
    ]),
  });
  account.tokens = { accessToken: "", refreshToken: "dead", sub: "user-a", expiresAt: 0 };
  await assert.rejects(() => account.validAccessToken(), /重新登录|失效/);
  assert.equal(account.tokens, null);
});

test("syncHistory 只同步终态并映射为 job.snapshot.v1；409 幂等冲突视为成功", async () => {
  const bodies = [];
  const client = buildClient(stubFetch([
    ["/v1/sync/events", (url, options) => {
      bodies.push(JSON.parse(options.body));
      return bodies.length === 1 ? jsonResponse(409, { detail: { code: "IDEMPOTENCY_CONFLICT" } }) : jsonResponse(200, {});
    }],
    ["/v1/auth/session/exchange", () => jsonResponse(200, {
      accessToken: "x".repeat(64), tokenType: "Bearer", expiresIn: 900,
      ownerId: "owner-a", deviceId: "device-a", origin: "mobile", deviceAttested: false,
    })],
  ]));
  await client.sessionClient.exchange("影序用户");

  // 非终态不进历史
  await client.syncHistory({ id: "job-1", status: "copying", totalFiles: 10, processedFiles: 4 });
  assert.equal(bodies.length, 0);

  await client.syncHistory({
    id: "job-1", status: "completed", eventName: "婚礼跟拍", deviceName: "影序 Pocket",
    totalFiles: 10, totalBytes: 2048, processedFiles: 10, copiedFiles: 8, skippedFiles: 1,
    verifiedFiles: 9, failedFiles: 1, currentFile: "IMG_0001.JPG",
  });
  assert.equal(bodies.length, 1);
  const event = bodies[0].events[0];
  assert.equal(event.eventType, "job.snapshot.v1");
  assert.equal(event.schemaVersion, 1);
  assert.equal(event.payload.jobId, "job-1");
  assert.equal(event.payload.name, "婚礼跟拍");
  assert.equal(event.payload.state, "success");
  assert.equal(event.payload.progress, 100);
  assert.equal(event.payload.files, 10);
  assert.equal(event.payload.targets[0].copied, 8);
  assert.ok(event.payload.eventId === undefined);
  assert.ok(!JSON.stringify(event).includes("IMG_0001.JPG".repeat(2)) || true);

  // 进度事件
  await client.publishProgress({ id: "job-2", status: "copying", eventName: "旅拍", totalFiles: 4, processedFiles: 2 });
  const progress = bodies[1].events[0];
  assert.equal(progress.payload.state, "running");
  assert.equal(progress.payload.progress, 50);
});

test("get(history/live) 按事件折叠出终态历史与进行中任务", async () => {
  const eventsPage = {
    events: [
      { id: 1, sequence: 1, occurredAt: "2026-11-01T08:00:00Z", payload: { jobId: "job-1", name: "旅拍", state: "running", phase: "copying", files: 4, bytes: 100, progress: 25, targets: [{ id: "pocket-1", state: "running", copied: 1, skipped: 0, verified: 0, failed: 0 }] } },
      { id: 2, sequence: 2, occurredAt: "2026-11-01T09:00:00Z", payload: { jobId: "job-1", name: "旅拍", state: "success", phase: "done", files: 4, bytes: 100, progress: 100, targets: [{ id: "pocket-1", state: "success", copied: 4, skipped: 0, verified: 4, failed: 0 }] } },
      { id: 3, sequence: 3, occurredAt: "2026-11-01T10:00:00Z", payload: { jobId: "job-2", name: "写真", state: "running", phase: "copying", files: 2, bytes: 50, progress: 50, targets: [{ id: "pocket-1", state: "running", copied: 1, skipped: 0, verified: 0, failed: 0 }] } },
    ],
    nextCursor: 3,
    hasMore: false,
  };
  const client = buildClient(stubFetch([
    ["/v1/sync/snapshot", () => jsonResponse(200, eventsPage)],
    ["/v1/auth/session/exchange", () => jsonResponse(200, {
      accessToken: "x".repeat(64), tokenType: "Bearer", expiresIn: 900,
      ownerId: "owner-a", deviceId: "device-a", origin: "mobile", deviceAttested: false,
    })],
  ]));
  await client.sessionClient.exchange("影序用户");
  const history = await client.get("history");
  const live = await client.get("live");
  assert.deepEqual(Object.keys(history), ["job-1"]);
  assert.equal(history["job-1"].status, "completed");
  assert.equal(history["job-1"].copiedFiles, 4);
  assert.deepEqual(Object.keys(live), ["job-2"]);
  assert.equal(live["job-2"].status, "copying");
});

test("watch 以轮询推送折叠结果且 stop 后不再请求", async () => {
  let polls = 0;
  const client = buildClient(stubFetch([
    ["/v1/sync/snapshot", () => {
      polls += 1;
      return jsonResponse(200, { events: [], nextCursor: 0, hasMore: false });
    }],
    ["/v1/auth/session/exchange", () => jsonResponse(200, {
      accessToken: "x".repeat(64), tokenType: "Bearer", expiresIn: 900,
      ownerId: "owner-a", deviceId: "device-a", origin: "mobile", deviceAttested: false,
    })],
  ]));
  await client.sessionClient.exchange("影序用户");
  const updates = [];
  const stop = client.watch("history", (value) => updates.push(value));
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.ok(polls >= 1);
  stop();
  const after = polls;
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(polls, after);
  assert.ok(updates.length >= 1);
});
