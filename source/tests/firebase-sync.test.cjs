const test = require("node:test");
const assert = require("node:assert/strict");
const { FirebaseSyncClient } = require("../src/services/firebase-sync.cjs");

test("Firebase authentication errors with detail suffixes show actionable guidance", async () => {
  const cases = [
    ["WEAK_PASSWORD : Password should be at least 6 characters", "密码未达到 Firebase 要求"],
    ["OPERATION_NOT_ALLOWED : PASSWORD sign-in is disabled", "尚未启用邮箱/密码登录"],
    ["API_KEY_INVALID", "Web API Key 无效"],
  ];
  for (const [code, expected] of cases) {
    const client = new FirebaseSyncClient({ apiKey: "public-key", databaseUrl: "https://yingxu.firebaseio.com",
      secureStore: { async set() {}, async get() { return null; }, async remove() {} }, deviceId: "phone-1",
      fetchImpl: async () => ({ ok: false, async json() { return { error: { message: code } }; } }) });
    await assert.rejects(client.signIn("photo@example.com", "temporary-input", true, "影序用户"), (error) => {
      assert.ok(error.message.includes(expected), error.message);
      assert.ok(!JSON.stringify(client).includes("temporary-input"));
      return true;
    });
  }
});

test("email registration persists username with Firebase profile update", async () => {
  const calls = [];
  const saved = new Map();
  const client = new FirebaseSyncClient({ apiKey: "public-key", databaseUrl: "https://yingxu.firebaseio.com",
    secureStore: { async set(key, value) { saved.set(key, value); }, async get(key) { return saved.get(key) || null; }, async remove(key) { saved.delete(key); } },
    deviceId: "phone-1", fetchImpl: async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push({ url: String(url), body });
      if (String(url).includes("accounts:signUp")) {
        return { ok: true, async json() { return { localId: "uid-a", email: "photo@example.test", idToken: "token-a", refreshToken: "refresh-a", expiresIn: "3600" }; } };
      }
      return { ok: true, async json() { return { displayName: "影序用户", idToken: "token-a2", refreshToken: "refresh-a2", expiresIn: "3600" }; } };
    } });

  const account = await client.signIn("photo@example.test", "temporary-password", true, "影序用户");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].body.displayName, undefined);
  assert.equal(calls[1].url.includes("accounts:update"), true);
  assert.equal(calls[1].body.displayName, "影序用户");
  assert.equal(account.displayName, "影序用户");
  assert.equal(JSON.stringify([...saved.values()]).includes("temporary-password"), false);
});

test("Firebase accounts only read under their own UID and keep refresh tokens in secure storage", async () => {
  const storageA = new Map();
  const storageB = new Map();
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    if (String(url).includes("accounts:signInWithPassword")) {
      const payload = JSON.parse(options.body);
      const id = payload.email.startsWith("a-") ? "uid-a" : "uid-b";
      return { ok: true, async json() { return { localId: id, idToken: `id-${id}`, refreshToken: `refresh-${id}`, expiresIn: "3600" }; } };
    }
    return { ok: true, async json() { return { history: {} }; } };
  };
  const clientA = new FirebaseSyncClient({ apiKey: "public-key", databaseUrl: "https://yingxu.firebaseio.com",
    secureStore: { async set(key, value) { storageA.set(key, value); }, async get(key) { return storageA.get(key) || null; }, async remove(key) { storageA.delete(key); } },
    deviceId: "device-a", fetchImpl });
  const clientB = new FirebaseSyncClient({ apiKey: "public-key", databaseUrl: "https://yingxu.firebaseio.com",
    secureStore: { async set(key, value) { storageB.set(key, value); }, async get(key) { return storageB.get(key) || null; }, async remove(key) { storageB.delete(key); } },
    deviceId: "device-b", fetchImpl });
  await clientA.signIn("a-photo@example.com", "user-password");
  await clientB.signIn("b-photo@example.com", "another-password");
  await clientA.get("history");
  await clientB.get("history");
  const readUrls = calls.filter((call) => call.options.method !== "POST").map((call) => call.url);
  assert.ok(readUrls.some((url) => url.includes("/users/uid-a/history.json")));
  assert.ok(readUrls.some((url) => url.includes("/users/uid-b/history.json")));
  assert.ok(!JSON.stringify([...storageA.values(), ...storageB.values()]).includes("user-password"));
  assert.ok(JSON.stringify([...storageA.values(), ...storageB.values()]).includes("refresh-uid-a"));
});

test("Firebase database paths reject traversal and invalid key characters", async () => {
  const client = new FirebaseSyncClient({ apiKey: "key", databaseUrl: "https://yingxu.firebaseio.com",
    secureStore: { async get() { return null; } }, deviceId: "device" });
  client.uid = "uid-a";
  await assert.rejects(client.get("../../uid-b/history"), /无效字段/);
  await assert.rejects(client.get("history/record.with.dots"), /无效字段/);
});

test("live progress omits the growing file manifest while history retains every file name", async () => {
  const calls = [];
  const client = new FirebaseSyncClient({ apiKey: "public-key", databaseUrl: "https://yingxu.firebaseio.com",
    secureStore: { async get() { return null; }, async set() {}, async remove() {} }, deviceId: "device-a",
    fetchImpl: async (url, options = {}) => {
      calls.push({ url: String(url), options });
      return { ok: true, async json() { return {}; } };
    } });
  client.uid = "uid-a";
  client.session = { idToken: "id-token", expiresAt: Date.now() + 3600_000 };
  const record = { id: "mobile-1", status: "copying", eventName: "山景", currentFile: "IMG_3.JPG",
    processedFiles: 2, totalFiles: 12, files: [
      { name: "IMG_1.JPG", size: 1024, status: "copied" },
      { name: "IMG_2.JPG", size: 2048, status: "copied" },
    ] };
  await client.publishProgress(record);
  await client.syncHistory(record);
  const liveCall = calls.find((call) => call.url.includes("/live/device-a.json"));
  const historyCall = calls.find((call) => call.url.includes("/history/mobile-1.json"));
  const live = JSON.parse(liveCall.options.body);
  const history = JSON.parse(historyCall.options.body);
  assert.equal(live.currentFile, "IMG_3.JPG");
  assert.equal(live.processedFiles, 2);
  assert.equal(Object.hasOwn(live, "files"), false);
  assert.deepEqual(history.files.map((file) => file.name), ["IMG_1.JPG", "IMG_2.JPG"]);
});
