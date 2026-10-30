const MAX_REQUEST_BYTES = 8 * 1024;
const MAX_RESPONSE_CHARS = 2 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 15_000;
const CONNECT_TIMEOUT_MS = 10_000;

function errorMessage(error, fallback) {
  return error && error.message ? error.message : fallback;
}

class PocketTcpClient {
  constructor(socketFactory) {
    if (typeof socketFactory !== "function") {
      throw new TypeError("PocketTcpClient requires a socket factory");
    }
    this.socketFactory = socketFactory;
    this.socket = null;
    this.buffer = "";
    this.nextId = 1;
    this.pending = new Map();
    this.closed = false;
  }

  connect(host, port = 18080) {
    if (this.socket) return Promise.reject(new Error("连接已创建"));
    return new Promise((resolve, reject) => {
      let settled = false;
      let timer;
      let socket;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback(value);
      };

      try {
        socket = this.socketFactory({ host, port, connectTimeout: CONNECT_TIMEOUT_MS });
        this.socket = socket;
        this.closed = false;
        timer = setTimeout(() => {
          finish(reject, new Error("连接超时；请确认手机与电脑在同一 Wi‑Fi"));
          this.close();
        }, CONNECT_TIMEOUT_MS + 1000);

        socket.on("connect", () => finish(resolve, undefined));
        socket.on("data", (chunk) => this._receive(chunk));
        socket.on("error", (error) => {
          finish(reject, error);
          this._failPending(new Error(errorMessage(error, "TCP 连接失败")));
          this.close();
        });
        socket.on("close", () => {
          this.closed = true;
          this.socket = null;
          this._failPending(new Error("桌面端连接已断开"));
          finish(reject, new Error("桌面端连接已断开"));
        });
      } catch (error) {
        finish(reject, error);
        this.close();
        return;
      }
    });
  }

  request(command, fields = {}) {
    if (!this.socket || this.closed) {
      return Promise.reject(new Error("尚未连接桌面端"));
    }
    const id = String(this.nextId++);
    const line = `${encodeURIComponent(JSON.stringify({ id, command, ...fields }))}\n`;
    if (line.length > MAX_REQUEST_BYTES) {
      return Promise.reject(new Error("请求内容超出限制"));
    }

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("桌面端响应超时"));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.socket.write(line, "ascii");
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  isClosed() {
    return this.closed || !this.socket;
  }

  _receive(chunk) {
    let text;
    try {
      text = typeof chunk === "string" ? chunk : chunk.toString("ascii");
    } catch (_) {
      this._protocolError("桌面端响应格式错误");
      return;
    }
    this.buffer += text;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      if (newline > MAX_RESPONSE_CHARS) {
        this._protocolError("桌面端返回内容超出限制");
        return;
      }
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (line) this._handleLine(line);
      if (this.closed) return;
      newline = this.buffer.indexOf("\n");
    }
    if (this.buffer.length > MAX_RESPONSE_CHARS) {
      this._protocolError("桌面端返回内容超出限制");
    }
  }

  _handleLine(line) {
    let response;
    try {
      response = JSON.parse(decodeURIComponent(line));
    } catch (_) {
      this._protocolError("桌面端响应格式错误");
      return;
    }
    if (!response || typeof response !== "object" || Array.isArray(response)) {
      this._protocolError("桌面端响应格式错误");
      return;
    }
    const pending = this.pending.get(String(response.id));
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(String(response.id));
    if (response.ok) pending.resolve(response);
    else pending.reject(new Error(response.error || "请求失败"));
  }

  _failPending(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  _protocolError(message) {
    this._failPending(new Error(message));
    this.close();
  }

  close() {
    const socket = this.socket;
    this.socket = null;
    this.closed = true;
    this._failPending(new Error("连接已关闭"));
    if (socket) {
      try {
        socket.destroy();
      } catch (_) {
        // The OS may already have closed the socket.
      }
    }
  }
}

module.exports = { PocketTcpClient };
