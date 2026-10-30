const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PocketTcpClient } = require("../src/services/pocket-tcp-client.cjs");

class FakeSocket extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.writes = [];
    this.destroyed = false;
  }

  write(data) {
    this.writes.push(data);
    return true;
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.emit("close");
  }

  connect() {
    setImmediate(() => this.emit("connect"));
  }

  respond(response, splitAt) {
    const frame = `${encodeURIComponent(JSON.stringify(response))}\n`;
    if (splitAt) {
      this.emit("data", Buffer.from(frame.slice(0, splitAt), "ascii"));
      this.emit("data", Buffer.from(frame.slice(splitAt), "ascii"));
    } else {
      this.emit("data", Buffer.from(frame, "ascii"));
    }
  }
}

function makeClient() {
  let socket;
  const client = new PocketTcpClient((options) => {
    socket = new FakeSocket(options);
    socket.connect();
    return socket;
  });
  return { client, getSocket: () => socket };
}

function readRequest(socket, index = 0) {
  return JSON.parse(decodeURIComponent(socket.writes[index].trim()));
}

test("connects and round-trips UTF-8 data using ASCII-framed JSON", async () => {
  const { client, getSocket } = makeClient();
  await client.connect("192.168.1.5", 18080);
  const socket = getSocket();
  assert.deepEqual(socket.options, { host: "192.168.1.5", port: 18080, connectTimeout: 10_000 });

  const resultPromise = client.request("notes.save", { token: "local-session", data: { event: "星空", notes: "湖边" } });
  const request = readRequest(socket);
  assert.equal(request.command, "notes.save");
  assert.equal(request.data.event, "星空");
  socket.respond({ id: request.id, ok: true, notes: request.data }, 9);
  assert.deepEqual(await resultPromise, { id: request.id, ok: true, notes: request.data });
  client.close();
  assert.equal(socket.destroyed, true);
});

test("rejects server errors and fails all pending requests on disconnect", async () => {
  const { client, getSocket } = makeClient();
  await client.connect("192.168.1.5");
  const socket = getSocket();
  const failedPromise = client.request("pair", { code: "123456" });
  const request = readRequest(socket);
  socket.respond({ id: request.id, ok: false, error: "配对码无效或已过期" });
  await assert.rejects(failedPromise, /配对码无效或已过期/);

  const pending = client.request("status", { token: "secret-in-memory" });
  const rejection = assert.rejects(pending, /桌面端连接已断开/);
  socket.emit("close");
  await rejection;
  assert.equal(client.closed, true);
});

test("closes on oversized response frames", async () => {
  const { client, getSocket } = makeClient();
  await client.connect("192.168.1.5");
  const socket = getSocket();
  const pending = client.request("status", { token: "local-session" });
  const rejection = assert.rejects(pending, /桌面端返回内容超出限制/);
  socket.emit("data", Buffer.from(`${"x".repeat(2 * 1024 * 1024 + 1)}\n`, "ascii"));
  await rejection;
  assert.equal(socket.destroyed, true);
});

test("closes on malformed or non-object response frames", async () => {
  for (const frame of ["not-json\n", `${encodeURIComponent("null")}\n`]) {
    const { client, getSocket } = makeClient();
    await client.connect("192.168.1.5");
    const socket = getSocket();
    const pending = client.request("status", { token: "local-session" });
    const rejection = assert.rejects(pending, /桌面端响应格式错误/);
    socket.emit("data", Buffer.from(frame, "ascii"));
    await rejection;
    assert.equal(socket.destroyed, true);
  }
});

test("rejects requests that exceed the desktop server frame limit", async () => {
  const { client, getSocket } = makeClient();
  await client.connect("192.168.1.5");
  const socket = getSocket();
  await assert.rejects(client.request("notes.save", { data: { notes: "x".repeat(9000) } }), /请求内容超出限制/);
  assert.equal(socket.writes.length, 0);
  client.close();
});
