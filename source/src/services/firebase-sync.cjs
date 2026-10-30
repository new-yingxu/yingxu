function publicMetadata(record) {
  const files = Array.isArray(record.files) ? record.files.map((file) => ({
    name: String(file.name || "").replace(/\\/g, "/").split("/").pop().slice(0, 255),
    size: Math.max(0, Number(file.size) || 0),
    status: String(file.status || "unknown").slice(0, 40),
    mediaType: String(file.mediaType || "").slice(0, 20),
  })) : [];
  return {
    id: String(record.id || "").slice(0, 100),
    deviceName: String(record.deviceName || record.sourceName || "影序设备").slice(0, 100),
    eventName: String(record.eventName || "").slice(0, 160),
    location: String(record.location || "").slice(0, 160),
    cameraDevice: String(record.cameraDevice || record.captureDevice || "").slice(0, 160),
    notes: String(record.notes || "").slice(0, 2000),
    targetName: String(record.destinationName || record.targetName || "").slice(0, 100),
    startedAt: record.startedAt || null,
    finishedAt: record.finishedAt || null,
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

function authError(message) {
  const value = String(message || "").split(":", 1)[0].trim();
  const known = {
    EMAIL_EXISTS: "此邮箱已注册。",
    EMAIL_NOT_FOUND: "此邮箱尚未注册，请先注册账号。",
    INVALID_EMAIL: "邮箱格式无效。",
    INVALID_PASSWORD: "邮箱或密码不正确。",
    INVALID_LOGIN_CREDENTIALS: "邮箱或密码不正确。",
    USER_DISABLED: "此账号已停用。",
    TOO_MANY_ATTEMPTS_TRY_LATER: "尝试次数过多，请稍后再试。",
    WEAK_PASSWORD: "密码未达到 Firebase 要求，请更换更强的密码。",
    OPERATION_NOT_ALLOWED: "Firebase 项目尚未启用邮箱/密码登录。",
    API_KEY_INVALID: "Firebase Web API Key 无效，请检查应用配置。",
    CONFIGURATION_NOT_FOUND: "Firebase Authentication 尚未完成项目配置。",
  };
  return known[value] || "Firebase 登录失败，请检查项目配置或网络。";
}

class FirebaseSyncClient {
  constructor({ apiKey, databaseUrl, secureStore, deviceId, fetchImpl = fetch, xhrFactory = () => new XMLHttpRequest() }) {
    this.apiKey = String(apiKey || "").trim();
    this.databaseUrl = String(databaseUrl || "").replace(/\/+$/, "");
    this.secureStore = secureStore;
    this.deviceId = String(deviceId || "mobile-device").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80);
    this.fetch = fetchImpl;
    this.xhrFactory = xhrFactory;
    this.session = null;
    this.uid = "";
  }

  get configured() {
    return Boolean(this.apiKey && this.databaseUrl && this.secureStore);
  }

  async _authRequest(endpoint, body) {
    if (!this.apiKey) throw new Error("尚未配置 Firebase 项目 API Key");
    const response = await this.fetch(`https://identitytoolkit.googleapis.com/v1/${endpoint}?key=${encodeURIComponent(this.apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, returnSecureToken: true }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(authError(data?.error?.message));
    return data;
  }

  async signIn(email, password, createAccount = false, displayName = "") {
    const endpoint = createAccount ? "accounts:signUp" : "accounts:signInWithPassword";
    const request = { email: email.trim(), password };
    const username = String(displayName || "").trim().slice(0, 40);
    if (createAccount && !username) throw new Error("注册时请填写用户名。");
    let data = await this._authRequest(endpoint, request);
    if ((createAccount || username) && username) {
      try {
        const updated = await this._authRequest("accounts:update", { idToken: data.idToken, displayName: username });
        data = { ...data, ...updated };
      } catch (_) {
        throw new Error(createAccount
          ? "账号已创建，但用户名暂未同步。请用登录按钮重试，并保留用户名字段。"
          : "账号已登录，但用户名暂未同步；请检查网络后重试。");
      }
    }
    this.session = {
      idToken: data.idToken,
      refreshToken: data.refreshToken,
      expiresAt: Date.now() + Math.max(60, Number(data.expiresIn) || 3600) * 1000,
      email: data.email || email.trim(),
      displayName: String(data.displayName || username || "").trim().slice(0, 40),
    };
    this.uid = data.localId;
    await this.secureStore.set("firebase.session", JSON.stringify({
      refreshToken: data.refreshToken, uid: data.localId, email: data.email || email.trim(), displayName: this.session.displayName,
    }));
    return { uid: this.uid, email: data.email || email.trim(), displayName: this.session.displayName };
  }

  async restoreSession() {
    if (!this.configured) return false;
    const saved = await this.secureStore.get("firebase.session");
    if (!saved) return false;
    let parsed;
    try { parsed = JSON.parse(saved); } catch (_) { return false; }
    if (!parsed.refreshToken || !parsed.uid) return false;
    const response = await this.fetch(`https://securetoken.googleapis.com/v1/token?key=${encodeURIComponent(this.apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `grant_type=refresh_token&refresh_token=${encodeURIComponent(parsed.refreshToken)}`,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.id_token) {
      await this.signOut();
      return false;
    }
    this.session = {
      idToken: data.id_token,
      refreshToken: data.refresh_token,
      expiresAt: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
      email: parsed.email || "",
      displayName: String(parsed.displayName || "").trim().slice(0, 40),
    };
    this.uid = data.user_id || parsed.uid;
    await this.secureStore.set("firebase.session", JSON.stringify({ refreshToken: data.refresh_token, uid: this.uid, email: this.session.email, displayName: this.session.displayName }));
    return true;
  }

  async _validToken() {
    if (this.session && this.session.expiresAt > Date.now() + 60_000) return this.session.idToken;
    if (!this.session) {
      const restored = await this.restoreSession();
      if (!restored) throw new Error("请先登录影序账号");
      return this.session.idToken;
    }
    const response = await this.fetch(`https://securetoken.googleapis.com/v1/token?key=${encodeURIComponent(this.apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `grant_type=refresh_token&refresh_token=${encodeURIComponent(this.session.refreshToken)}`,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.id_token) throw new Error("登录已过期，请重新登录影序账号");
    this.session = {
      idToken: data.id_token,
      refreshToken: data.refresh_token,
      expiresAt: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
      email: this.session.email || "",
      displayName: this.session.displayName || "",
    };
    await this.secureStore.set("firebase.session", JSON.stringify({
      refreshToken: data.refresh_token,
      uid: this.uid,
      email: this.session.email,
      displayName: this.session.displayName,
    }));
    return data.id_token;
  }

  async signOut() {
    this.session = null;
    this.uid = "";
    if (this.secureStore) await this.secureStore.remove("firebase.session");
  }

  _url(path, token) {
    if (!this.databaseUrl || !this.uid) throw new Error("云同步尚未登录或配置");
    const segments = String(path || "").split("/");
    if (!segments.length || segments.some((segment) => !/^[A-Za-z0-9_-]{1,200}$/.test(segment))) {
      throw new Error("Firebase 数据路径包含无效字段");
    }
    const normalized = segments.map(encodeURIComponent).join("/");
    return `${this.databaseUrl}/users/${encodeURIComponent(this.uid)}/${normalized}.json?auth=${encodeURIComponent(token)}`;
  }

  async get(path) {
    this._url(path, "");
    const token = await this._validToken();
    const response = await this.fetch(this._url(path, token));
    if (!response.ok) throw new Error("读取云端历史失败");
    return response.json();
  }

  async put(path, value) {
    this._url(path, "");
    const token = await this._validToken();
    const response = await this.fetch(this._url(path, token), {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
    });
    if (!response.ok) throw new Error("同步记录到云端失败");
    return response.json();
  }

  async syncHistory(record) {
    const metadata = publicMetadata(record);
    if (!metadata.id) return;
    await this.put(`history/${metadata.id}`, { ...metadata, deviceId: this.deviceId });
  }

  async publishProgress(record) {
    const metadata = publicMetadata(record);
    const liveMetadata = { ...metadata };
    delete liveMetadata.files;
    await this.put(`live/${this.deviceId}`, { ...liveMetadata, deviceId: this.deviceId, updatedAt: Date.now() });
    if (["completed", "partial", "cancelled", "failed"].includes(metadata.status)) {
      await this.syncHistory(record);
      await this.put(`live/${this.deviceId}`, null);
    }
  }

  /** @param {(value: any) => void} onValue @param {(error: Error) => void} [onError] */
  watch(path, onValue, onError = () => {}) {
    let stopped = false;
    let xhr;
    let cursor = 0;
    let buffer = "";
    let value = null;
    const reconnect = async () => {
      if (stopped) return;
      try {
        const token = await this._validToken();
        const url = this._url(path, token);
        xhr = this.xhrFactory();
        xhr.open("GET", url, true);
        xhr.setRequestHeader("Accept", "text/event-stream");
        xhr.onprogress = () => {
          const chunk = xhr.responseText.slice(cursor);
          cursor = xhr.responseText.length;
          buffer += chunk;
          let delimiter;
          while ((delimiter = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, delimiter);
            buffer = buffer.slice(delimiter + 2);
            const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim();
            const data = block.match(/^data:\s*(.+)$/m)?.[1];
            if (event === "put" || event === "patch") {
              try {
                const payload = JSON.parse(data || "null");
                const path = String(payload?.path || "/").split("/").filter(Boolean);
                if (!path.length && event === "put") value = payload?.data ?? null;
                else if (path.length) {
                  let target = value;
                  if (!target || typeof target !== "object") value = target = {};
                  for (const segment of path.slice(0, -1)) target = target[segment] ||= {};
                  if (payload?.data === null) delete target[path[path.length - 1]];
                  else target[path[path.length - 1]] = payload?.data;
                } else if (payload?.data && typeof payload.data === "object") {
                  value = { ...(value || {}), ...payload.data };
                }
                onValue(value);
              } catch (_) { onError(new Error("云端实时数据格式错误")); }
            } else if (event === "cancel" || event === "auth_revoked") {
              onError(new Error("云端访问被拒绝，请检查 Firebase 数据库规则"));
              xhr.abort();
            }
          }
        };
        xhr.onerror = () => { if (!stopped) onError(new Error("云端实时连接中断")); };
        xhr.onload = () => { if (!stopped) setTimeout(reconnect, 1500); };
        xhr.send();
      } catch (error) {
        if (!stopped) {
          onError(error);
          setTimeout(reconnect, 5000);
        }
      }
    };
    reconnect();
    return () => {
      stopped = true;
      if (xhr) xhr.abort();
    };
  }
}

module.exports = { FirebaseSyncClient, publicMetadata };
