/**
 * 影序 Pocket 云端客户端（CloudBase 托管）。
 *
 * 身份：CloudBase HTTP API（与桌面 Rust 宿主同一协议）——
 *   POST {authOrigin}/auth/v1/signin?client_id=   {username, password} → TokenResponse
 *   POST {authOrigin}/auth/v1/token               {grant_type:"refresh_token", ...}（刷新令牌一次性轮换）
 *   POST {authOrigin}/auth/v1/verification        {email, target:"USER"|"ANY"}
 *   POST {authOrigin}/auth/v1/verification/verify {verification_id, verification_code} → verification_token
 *   POST {authOrigin}/auth/v1/signup?client_id=   {email, password, verification_token}
 *   POST {authOrigin}/auth/v1/user/signout?client_id=
 * 会话：影序身份服务 /v1/auth/session/exchange|renew|logout，换取 15 分钟设备短会话。
 * 同步：/v1/sync/events + /v1/sync/snapshot 事件日志，只同步 job.snapshot.v1 任务摘要
 *       （原片、路径与笔记永不上云，遵循仓库边界声明）。
 *
 * 配置（Expo 环境变量，缺一即视为未配置，不内嵌任何密钥）：
 *   EXPO_PUBLIC_CLOUDBASE_AUTH_ORIGIN   https://{env}.api.tcloudbasegateway.com
 *   EXPO_PUBLIC_CLOUDBASE_CLIENT_ID     移动端客户端 ID（服务端映射 origin=mobile）
 *   EXPO_PUBLIC_IDENTITY_API_BASE_URL   影序身份服务 HTTPS 地址
 */

const SESSION_LIFETIME_GUARD_MS = 60_000;

function str(value) {
  return typeof value === "string" ? value : "";
}

function publicMetadata(record) {
  const files = Array.isArray(record.files) ? record.files.length : 0;
  return {
    id: String(record.id || "").slice(0, 100),
    deviceName: String(record.deviceName || record.sourceName || "影序设备").slice(0, 100),
    eventName: String(record.eventName || "").slice(0, 160),
    status: String(record.status || "unknown").slice(0, 40),
    currentFile: String(record.currentFile || "").replace(/\\/g, "/").split("/").pop().slice(0, 255),
    totalFiles: Math.max(0, Number(record.totalFiles) || 0),
    totalBytes: Math.max(0, Number(record.totalBytes) || 0),
    processedFiles: Math.max(0, Number(record.processedFiles) || 0),
    copiedFiles: Math.max(0, Number(record.copiedFiles) || 0),
    skippedFiles: Math.max(0, Number(record.skippedFiles) || 0),
    verifiedFiles: Math.max(0, Number(record.verifiedFiles) || 0),
    failedFiles: Math.max(0, Number(record.failedFiles) || 0),
    files,
  };
}

const STATE_TO_CLOUDBASE = {
  queued: "queued",
  scanning: "queued",
  copying: "running",
  running: "running",
  paused: "paused",
  completed: "success",
  success: "success",
  partial: "partial",
  cancelled: "cancelled",
  failed: "failed",
  error: "failed",
};

const STATE_FROM_CLOUDBASE = {
  queued: "queued",
  running: "copying",
  paused: "paused",
  success: "completed",
  partial: "partial",
  cancelled: "cancelled",
  failed: "failed",
};

const TERMINAL_CLOUDBASE_STATES = ["success", "partial", "cancelled", "failed"];

function authErrorDescription(body) {
  const code = str(body && body.error);
  const known = {
    invalid_grant: "账号或登录状态已失效，请重新登录。",
    invalid_client: "应用客户端配置无效，请联系影序支持。",
    invalid_request: "登录请求不完整，请重试。",
  };
  if (known[code]) return known[code];
  return "登录服务暂时不可用，请稍后重试。";
}

class CloudBaseAccount {
  /**
   * @param {{ authOrigin: string, clientId: string, secureStore: object, fetchImpl?: typeof fetch }} options
   */
  constructor({ authOrigin, clientId, secureStore, fetchImpl = fetch }) {
    this.authOrigin = String(authOrigin || "").replace(/\/+$/, "");
    this.clientId = String(clientId || "").trim();
    this.secureStore = secureStore;
    this.fetch = fetchImpl;
    this.tokens = null; // { accessToken, refreshToken, sub, expiresAt }
  }

  get configured() {
    return /^https:\/\//.test(this.authOrigin) && Boolean(this.clientId) && Boolean(this.secureStore);
  }

  static STORE_KEY = "yingxu.account.v1";

  _url(path, withClientIdQuery = true) {
    if (!/^https:\/\//.test(this.authOrigin)) throw new Error("尚未配置 CloudBase 登录域名");
    let url = `${this.authOrigin}${path}`;
    if (withClientIdQuery) url += `${url.includes("?") ? "&" : "?"}client_id=${encodeURIComponent(this.clientId)}`;
    return url;
  }

  async _post(url, body, bearer = null) {
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    const response = await this.fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(authErrorDescription(data));
    return data;
  }

  _acceptTokenResponse(data) {
    if (!data || str(data.token_type).toLowerCase() !== "bearer" || !str(data.access_token)
      || !str(data.refresh_token) || !(Number(data.expires_in) > 0) || !str(data.sub)) {
      throw new Error("登录服务返回无效响应，请稍后重试。");
    }
    this.tokens = {
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      sub: data.sub,
      expiresAt: Date.now() + Math.max(60, Number(data.expires_in)) * 1000,
    };
    return this.tokens;
  }

  async _persist() {
    if (!this.tokens || !this.secureStore) return;
    await this.secureStore.set(CloudBaseAccount.STORE_KEY, JSON.stringify({
      refreshToken: this.tokens.refreshToken,
      sub: this.tokens.sub,
    }));
  }

  async signIn(email, password) {
    const data = await this._post(this._url("/auth/v1/signin"), { username: email.trim(), password });
    this._acceptTokenResponse(data);
    await this._persist();
    return { sub: this.tokens.sub };
  }

  async requestEmailCode(email, forSignup = false) {
    const data = await this._post(this._url("/auth/v1/verification"), {
      email: email.trim(),
      target: forSignup ? "ANY" : "USER",
    });
    if (!str(data.verification_id)) throw new Error("验证码发送失败，请稍后重试。");
    return { verificationId: data.verification_id, expiresIn: Number(data.expires_in) || 0 };
  }

  async verifyEmailCode(verificationId, verificationCode) {
    const data = await this._post(this._url("/auth/v1/verification/verify"), {
      verification_id: String(verificationId || "").trim(),
      verification_code: String(verificationCode || "").trim(),
    });
    if (!str(data.verification_token)) throw new Error("邮箱验证失败，请检查验证码并重试。");
    return { verificationToken: data.verification_token };
  }

  async signUp(email, password, verificationToken) {
    const data = await this._post(this._url("/auth/v1/signup"), {
      email: email.trim(),
      password,
      verification_token: verificationToken,
    });
    this._acceptTokenResponse(data);
    await this._persist();
    return { sub: this.tokens.sub };
  }

  /** 一次性轮换刷新令牌；失败即要求重新登录。 */
  async refresh() {
    if (!this.tokens) {
      const restored = await this.restoreTokens();
      if (!restored) throw new Error("请先登录影序账号");
    }
    let refreshToken = this.tokens.refreshToken;
    try {
      const data = await this._post(this._url("/auth/v1/token", false), {
        grant_type: "refresh_token",
        refresh_token: refreshToken,
        client_id: this.clientId,
      });
      this._acceptTokenResponse(data);
    } catch (error) {
      this.tokens = null;
      await this.secureStore.remove(CloudBaseAccount.STORE_KEY).catch(() => {});
      throw error instanceof Error && error.message ? error : new Error("登录已过期，请重新登录影序账号");
    }
    await this._persist();
    return this.tokens.accessToken;
  }

  async restoreTokens() {
    if (!this.secureStore) return false;
    const saved = await this.secureStore.get(CloudBaseAccount.STORE_KEY).catch(() => null);
    if (!saved) return false;
    let parsed;
    try { parsed = JSON.parse(saved); } catch { return false; }
    if (!str(parsed.refreshToken) || !str(parsed.sub)) return false;
    this.tokens = {
      accessToken: "",
      refreshToken: parsed.refreshToken,
      sub: parsed.sub,
      expiresAt: 0,
    };
    return true;
  }

  /** 返回可用的 CloudBase access token；内存里没有有效值时走一次刷新。 */
  async validAccessToken() {
    if (this.tokens && this.tokens.accessToken && this.tokens.expiresAt > Date.now() + SESSION_LIFETIME_GUARD_MS) {
      return this.tokens.accessToken;
    }
    return this.refresh();
  }

  async signOut() {
    if (this.tokens && this.tokens.accessToken) {
      await this._post(this._url("/auth/v1/user/signout"), {}, this.tokens.accessToken).catch(() => {});
    }
    this.tokens = null;
    if (this.secureStore) await this.secureStore.remove(CloudBaseAccount.STORE_KEY).catch(() => {});
  }
}

class YingxuSession {
  /**
   * @param {{ identityApiUrl: string, account: CloudBaseAccount, secureStore: object, fetchImpl?: typeof fetch }} options
   */
  constructor({ identityApiUrl, account, secureStore, fetchImpl = fetch }) {
    this.identityApiUrl = String(identityApiUrl || "").replace(/\/+$/, "");
    this.account = account;
    this.secureStore = secureStore;
    this.fetch = fetchImpl;
    this.session = null; // { accessToken, ownerId, deviceId, expiresAt, nextSequence }
    this.profile = { email: "", displayName: "" };
  }

  get configured() {
    return /^https:\/\//.test(this.identityApiUrl) && Boolean(this.account);
  }

  static STORE_KEY = "yingxu.device-session.v1";

  async _post(path, bearer, extraHeaders = {}, body = {}) {
    const response = await this.fetch(`${this.identityApiUrl}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${bearer}`,
        "Content-Type": "application/json",
        Accept: "application/json",
        ...extraHeaders,
      },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = str(data && data.detail && data.detail.code);
      throw new Error(code ? `云端会话被拒绝（${code}）` : "影序云服务暂时不可用，请稍后重试。");
    }
    return data;
  }

  async _persist() {
    if (!this.session || !this.secureStore) return;
    await this.secureStore.set(YingxuSession.STORE_KEY, JSON.stringify({
      accessToken: this.session.accessToken,
      ownerId: this.session.ownerId,
      deviceId: this.session.deviceId,
      expiresAt: this.session.expiresAt,
      nextSequence: this.session.nextSequence,
      email: this.profile.email,
      displayName: this.profile.displayName,
    }));
  }

  async exchange(displayName = "") {
    if (!this.account) throw new Error("云端账号服务不可用");
    const accountToken = await this.account.validAccessToken();
    const name = String(displayName || "").trim().slice(0, 120) || "影序 Pocket";
    const reply = await this._post("/v1/auth/session/exchange", accountToken, {}, { displayName: name });
    if (str(reply.accessToken).length < 40 || reply.tokenType !== "Bearer" || Number(reply.expiresIn) !== 900
      || !str(reply.ownerId) || !str(reply.deviceId) || !["desktop", "mobile"].includes(reply.origin)) {
      throw new Error("影序云服务返回的会话不符合协议，请联系支持。");
    }
    this.session = {
      accessToken: reply.accessToken,
      ownerId: reply.ownerId,
      deviceId: reply.deviceId,
      expiresAt: Date.now() + Number(reply.expiresIn) * 1000,
      nextSequence: 1,
    };
    await this._persist();
    return { ...this.session };
  }

  async restore() {
    if (!this.configured) return false;
    const saved = await this.secureStore.get(YingxuSession.STORE_KEY).catch(() => null);
    if (saved) {
      let parsed;
      try { parsed = JSON.parse(saved); } catch { parsed = null; }
      if (parsed && str(parsed.accessToken) && str(parsed.ownerId) && Number(parsed.expiresAt) > Date.now() + SESSION_LIFETIME_GUARD_MS) {
        this.session = {
          accessToken: parsed.accessToken,
          ownerId: parsed.ownerId,
          deviceId: parsed.deviceId,
          expiresAt: Number(parsed.expiresAt),
          nextSequence: Math.max(1, Number(parsed.nextSequence) || 1),
        };
        this.profile = { email: str(parsed.email), displayName: str(parsed.displayName) };
        return true;
      }
    }
    // 短会话过期：用 CloudBase 账号令牌静默重换（账号令牌自身会按需刷新）。
    try {
      await this.account.validAccessToken();
    } catch {
      return false;
    }
    await this.exchange(this.profile.displayName);
    return Boolean(this.session);
  }

  async renew() {
    if (!this.session) throw new Error("请先登录影序账号");
    const accountToken = await this.account.validAccessToken();
    const previous = this.session.accessToken;
    const reply = await this._post("/v1/auth/session/renew", accountToken, { "X-Yingxu-Session": previous });
    if (reply.ownerId !== this.session.ownerId || reply.deviceId !== this.session.deviceId) {
      this.session = null;
      await this.secureStore.remove(YingxuSession.STORE_KEY).catch(() => {});
      throw new Error("续期身份与当前设备不一致，请重新登录");
    }
    this.session = {
      ...this.session,
      accessToken: reply.accessToken,
      expiresAt: Date.now() + Number(reply.expiresIn) * 1000,
    };
    await this._persist();
    return { ...this.session };
  }

  /** 会话令牌；临期自动续期，失败时尝试静默重换。 */
  async validSessionToken() {
    if (!this.session || this.session.expiresAt <= Date.now() + SESSION_LIFETIME_GUARD_MS) {
      if (this.session && this.session.expiresAt > Date.now()) {
        await this.renew().catch(() => this.exchange(this.profile.displayName));
      } else {
        const restored = await this.restore();
        if (!restored) throw new Error("请先登录影序账号");
      }
    }
    return this.session.accessToken;
  }

  async logout() {
    if (this.session) {
      await this._post("/v1/auth/logout", this.session.accessToken).catch(() => {});
    }
    this.session = null;
    if (this.secureStore) await this.secureStore.remove(YingxuSession.STORE_KEY).catch(() => {});
  }
}

/**
 * 与旧 FirebaseSyncClient 相同的对外接口（signIn/restoreSession/signOut/session/uid/
 * syncHistory/publishProgress/get/watch），后端换成 CloudBase + 影序同步事件日志。
 */
class YingxuCloudClient {
  /**
   * @param {{ account: CloudBaseAccount, session: YingxuSession, deviceId: string, fetchImpl?: typeof fetch }} options
   */
  constructor({ account, session, deviceId, fetchImpl = fetch }) {
    this.account = account;
    this.sessionClient = session;
    this.deviceId = String(deviceId || "mobile-device").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80);
    this.fetch = fetchImpl;
    this.events = new Map(); // jobId → latest event body（本进程内的增量缓存）
    this.cursor = 0;
    this.folded = false;
  }

  get configured() {
    return Boolean(this.account && this.account.configured && this.sessionClient && this.sessionClient.configured);
  }

  get uid() {
    return this.sessionClient.session ? this.sessionClient.session.ownerId : "";
  }

  get session() {
    const current = this.sessionClient.session;
    if (!current) return null;
    return {
      ownerId: current.ownerId,
      deviceId: current.deviceId,
      displayName: this.sessionClient.profile.displayName,
      email: this.sessionClient.profile.email,
      expiresAt: current.expiresAt,
    };
  }

  async signIn(email, password, createAccount = false, displayName = "", verificationCode = "") {
    const name = String(displayName || "").trim().slice(0, 40);
    if (createAccount) {
      if (!name) throw new Error("注册时请填写用户名。");
      if (!String(verificationCode || "").trim()) throw new Error("请先获取并填写邮箱验证码。");
      const challenge = await this.account.requestEmailCode(email, true);
      const { verificationToken } = await this.account.verifyEmailCode(challenge.verificationId, verificationCode);
      await this.account.signUp(email, password, verificationToken);
    } else {
      await this.account.signIn(email, password);
    }
    if (name) this.sessionClient.profile.displayName = name;
    this.sessionClient.profile.email = email.trim();
    await this.sessionClient.exchange(name || "影序 Pocket");
    this.events.clear();
    this.cursor = 0;
    this.folded = false;
    return { uid: this.uid, email: email.trim(), displayName: name };
  }

  async sendEmailCode(email, purpose = "register") {
    return this.account.requestEmailCode(email, purpose === "register");
  }

  async restoreSession() {
    if (!this.configured) return false;
    const restored = await this.sessionClient.restore();
    if (restored) {
      this.events.clear();
      this.cursor = 0;
      this.folded = false;
    }
    return restored;
  }

  async signOut() {
    await this.sessionClient.logout();
    await this.account.signOut();
    this.events.clear();
    this.cursor = 0;
    this.folded = false;
  }

  _jobSnapshotEvent(record) {
    const metadata = publicMetadata(record);
    if (!metadata.id) throw new Error("备份记录缺少 id，无法同步");
    const state = STATE_TO_CLOUDBASE[metadata.status] || "queued";
    const progress = state === "success" || metadata.status === "completed"
      ? 100
      : metadata.totalFiles > 0
        ? Math.min(100, Math.round((metadata.processedFiles / metadata.totalFiles) * 100))
        : 0;
    const sequence = this.sessionClient.session ? this.sessionClient.session.nextSequence : 1;
    if (this.sessionClient.session) this.sessionClient.session.nextSequence = sequence + 1;
    return {
      schemaVersion: 1,
      eventId: `${metadata.id}:${state}:${metadata.processedFiles}`.slice(0, 500),
      deviceId: this.sessionClient.session ? this.sessionClient.session.deviceId : this.deviceId,
      sequence,
      eventType: "job.snapshot.v1",
      occurredAt: new Date().toISOString(),
      payload: {
        jobId: metadata.id,
        name: (metadata.eventName || metadata.deviceName || "备份任务").slice(0, 500) || "备份任务",
        state,
        phase: metadata.currentFile || metadata.status || "backup",
        files: metadata.totalFiles,
        bytes: metadata.totalBytes,
        progress,
        targets: [{
          id: this.deviceId,
          state,
          copied: metadata.copiedFiles,
          skipped: metadata.skippedFiles,
          verified: metadata.verifiedFiles,
          failed: metadata.failedFiles,
        }],
      },
    };
  }

  async _ingest(event) {
    const token = await this.sessionClient.validSessionToken();
    const response = await this.fetch(`${this.sessionClient.identityApiUrl}/v1/sync/events`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ events: [event] }),
    });
    if (!response.ok) {
      // 同一事件重试时（eventId 幂等键相同）服务端按已入库处理，视为成功。
      if (response.status === 409) return;
      const data = await response.json().catch(() => ({}));
      const code = str(data && data.detail && data.detail.code);
      throw new Error(code === "DEVICE_MISMATCH" || code === "INVALID_SESSION"
        ? "云端会话已失效，请重新登录影序账号"
        : "同步记录到云端失败，稍后会自动重试。");
    }
  }

  async syncHistory(record) {
    const event = this._jobSnapshotEvent(record);
    const state = event.payload.state;
    if (!TERMINAL_CLOUDBASE_STATES.includes(state)) return; // 只有终态进入历史
    await this._ingest(event);
    this._foldEvents([event]);
  }

  async publishProgress(record) {
    const event = this._jobSnapshotEvent(record);
    await this._ingest(event);
    this._foldEvents([event]);
  }

  /** 拉取账户事件日志（增量游标）并折叠成 {history, live} 两张映射表。 */
  async _pull() {
    const token = await this.sessionClient.validSessionToken();
    let guard = 0;
    for (;;) {
      if (++guard > 100) throw new Error("云端事件过多，请稍后重试");
      const response = await this.fetch(
        `${this.sessionClient.identityApiUrl}/v1/sync/snapshot?after_id=${this.cursor}&limit=500`,
        { headers: { Authorization: `Bearer ${token}`, Accept: "application/json" } },
      );
      if (!response.ok) throw new Error("读取云端历史失败");
      const data = await response.json().catch(() => ({}));
      const events = Array.isArray(data.events) ? data.events : [];
      this._foldEvents(events);
      if (!data.hasMore) break;
      this.cursor = Number(data.nextCursor) || this.cursor;
    }
    this.folded = true;
  }

  _foldEvents(events) {
    for (const event of events) {
      const payload = event && event.payload;
      if (!payload || !str(payload.jobId)) continue;
      this.cursor = Math.max(this.cursor, Number(event.id) || this.cursor);
      // 拉取按事件 id 升序，后到的事件总是更新，直接覆盖。
      this.events.set(payload.jobId, { ...event, receivedAt: Date.now() });
    }
  }

  /** 兼容旧接口：get("history") / get("live") 返回 {jobId: 记录} 映射。 */
  async get(path) {
    if (path !== "history" && path !== "live") throw new Error("不支持的云端读取路径");
    await this._pull();
    const result = {};
    for (const [jobId, event] of this.events) {
      const payload = event.payload;
      const record = {
        id: jobId,
        eventName: payload.name,
        status: STATE_FROM_CLOUDBASE[payload.state] || payload.state,
        currentFile: payload.phase,
        totalFiles: payload.files,
        totalBytes: payload.bytes,
        processedFiles: Math.round((Number(payload.progress) || 0) / 100 * payload.files),
        copiedFiles: payload.targets && payload.targets[0] ? payload.targets[0].copied : 0,
        skippedFiles: payload.targets && payload.targets[0] ? payload.targets[0].skipped : 0,
        verifiedFiles: payload.targets && payload.targets[0] ? payload.targets[0].verified : 0,
        failedFiles: payload.targets && payload.targets[0] ? payload.targets[0].failed : 0,
        deviceName: payload.targets && payload.targets[0] ? payload.targets[0].id : "影序设备",
        startedAt: event.occurredAt || null,
        finishedAt: TERMINAL_CLOUDBASE_STATES.includes(payload.state) ? event.occurredAt || null : null,
      };
      const terminal = TERMINAL_CLOUDBASE_STATES.includes(payload.state);
      if (path === "history" && terminal) result[jobId] = record;
      if (path === "live" && !terminal) result[jobId] = record;
    }
    return result;
  }

  /**
   * 兼容旧接口的订阅：影序同步 API 是拉取式事件日志，这里用轮询近似
   * Firebase 的实时推送（默认 5 秒；关闭 App 或调用返回的 stop 函数即停）。
   */
  watch(path, onValue, onError = () => {}) {
    if (path !== "history" && path !== "live") {
      onError(new Error("不支持的云端订阅路径"));
      return () => {};
    }
    let stopped = false;
    let timer = null;
    const tick = async () => {
      if (stopped) return;
      try {
        onValue(await this.get(path));
        timer = setTimeout(tick, 5000);
      } catch (error) {
        if (!stopped) {
          onError(error instanceof Error ? error : new Error("云端同步中断"));
          timer = setTimeout(tick, 10000);
        }
      }
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }
}

/** 组装完整的云端客户端；任一配置缺失返回 configured=false 的实例（保持可离线使用）。 */
function createYingxuCloudClient({ secureStore, deviceId, fetchImpl } = {}) {
  const account = new CloudBaseAccount({
    authOrigin: process.env.EXPO_PUBLIC_CLOUDBASE_AUTH_ORIGIN || "",
    clientId: process.env.EXPO_PUBLIC_CLOUDBASE_CLIENT_ID || "",
    secureStore,
    fetchImpl,
  });
  const session = new YingxuSession({
    identityApiUrl: process.env.EXPO_PUBLIC_IDENTITY_API_BASE_URL || "",
    account,
    secureStore,
    fetchImpl,
  });
  return new YingxuCloudClient({ account, session, deviceId, fetchImpl });
}

module.exports = {
  CloudBaseAccount,
  YingxuSession,
  YingxuCloudClient,
  createYingxuCloudClient,
  publicMetadata,
  STATE_TO_CLOUDBASE,
  STATE_FROM_CLOUDBASE,
};
