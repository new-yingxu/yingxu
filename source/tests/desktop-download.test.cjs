const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { DesktopDownloadEngine, PAGE_SIZE, CHUNK_SIZE } = require("../src/services/desktop-download.cjs");

test("lists desktop media pages without losing the next page", async () => {
  const calls = [];
  const engine = new DesktopDownloadEngine({
    client: { async request(command, request) {
      calls.push([command, request.offset]);
      return request.offset === 0
        ? { items: [{ source_id: "a" }], total: 2, next_offset: PAGE_SIZE }
        : { items: [{ source_id: "b" }], total: 2, next_offset: null };
    } },
    token: "paired", mediaAccess: {}, destinationRoot: "folder://target",
  });
  assert.deepEqual((await engine.listFiles()).map((item) => item.source_id), ["a", "b"]);
  assert.deepEqual(calls, [["desktop.files.list", 0], ["desktop.files.list", PAGE_SIZE]]);
});

test("downloads a verified computer file in chunks to the authorized mobile folder", async () => {
  const payload = Buffer.from("desktop video bytes".repeat(120000));
  const sha256 = crypto.createHash("sha256").update(payload).digest("hex");
  let offset = 0;
  const calls = [];
  const client = {
    isClosed: () => false,
    async request(command, request) {
      calls.push(command);
      if (command === "desktop.file.begin") return { size: payload.length, sha256 };
      if (command === "desktop.file.chunk") {
        const data = payload.subarray(request.offset, request.offset + request.length);
        return { data: data.toString("base64"), bytes_read: data.length };
      }
      if (command === "desktop.file.finish") return { size: payload.length, sha256 };
      throw new Error(`unexpected command: ${command}`);
    },
  };
  let incoming = Buffer.alloc(0);
  let receivedPath = "";
  const mediaAccess = {
    async beginIncomingWriteAsync(root, relative, size, hash) {
      receivedPath = `${root}/${relative}`;
      assert.equal(size, payload.length);
      assert.equal(hash, sha256);
      return { status: "receiving", transferId: "native-1" };
    },
    async appendIncomingChunkAsync(id, encoded) {
      assert.equal(id, "native-1");
      incoming = Buffer.concat([incoming, Buffer.from(encoded, "base64")]);
      offset = incoming.length;
      return { received: offset };
    },
    async finishIncomingWriteAsync() {
      assert.equal(crypto.createHash("sha256").update(incoming).digest("hex"), sha256);
      return { status: "copied", sha256, size: incoming.length };
    },
    async abortIncomingWriteAsync() { throw new Error("unexpected abort"); },
  };
  const updates = [];
  const engine = new DesktopDownloadEngine({ client, token: "paired", mediaAccess,
    destinationRoot: "folder://phone", eventName: "山景", onUpdate: (state) => updates.push(state) });
  const result = await engine.run([{ source_id: "desktop-1", name: "clip.mov", media_type: "video",
    size: payload.length, modified_at: Date.UTC(2026, 0, 2), captured_at: "2026-01-02T03:04:05" }]);
  assert.equal(result.status, "completed");
  assert.equal(result.copiedFiles, 1);
  assert.equal(result.verifiedFiles, 1);
  assert.equal(incoming.length, payload.length);
  assert.match(receivedPath, /2026年01月02日\/山景\/手机拍摄\/照片及视频原片\/视频\/clip\.mov$/);
  assert.ok(calls.filter((command) => command === "desktop.file.chunk").length > 1);
  assert.ok(updates.some((state) => state.bytesCopied === payload.length));
  assert.equal(CHUNK_SIZE, 1024 * 1024);
});

test("keeps an interrupted download queued and discards its incomplete mobile temp file", async () => {
  const aborted = [];
  const engine = new DesktopDownloadEngine({
    client: { isClosed: () => true, async request(command) {
      if (command === "desktop.file.begin") return { size: 8, sha256: "a".repeat(64) };
      if (command === "desktop.file.chunk") throw new Error("桌面端连接已断开");
      throw new Error("unexpected");
    } },
    token: "paired",
    mediaAccess: {
      async beginIncomingWriteAsync() { return { status: "receiving", transferId: "partial" }; },
      async abortIncomingWriteAsync(id) { aborted.push(id); },
    },
    destinationRoot: "folder://phone",
  });
  const result = await engine.run([{ source_id: "a", name: "photo.jpg", media_type: "photo", size: 8 }]);
  assert.equal(result.status, "queued");
  assert.deepEqual(aborted, ["partial"]);
  assert.equal(result.processedFiles, 0);
});

test("queues an active computer import when the app backgrounds and persists resumable file identity", async () => {
  let engine;
  const aborted = [];
  let releaseChunk;
  const client = {
    isClosed: () => false,
    async request(command) {
      if (command === "desktop.file.begin") return { size: 9, sha256: "b".repeat(64) };
      if (command === "desktop.file.chunk") return new Promise((resolve) => { releaseChunk = () => resolve({ data: Buffer.from("x").toString("base64"), bytes_read: 1 }); });
      throw new Error("unexpected");
    },
  };
  engine = new DesktopDownloadEngine({ client, token: "paired", destinationRoot: "folder://phone",
    mediaAccess: {
      async beginIncomingWriteAsync() { return { status: "receiving", transferId: "temporary" }; },
      async appendIncomingChunkAsync() {},
      async abortIncomingWriteAsync(id) { aborted.push(id); },
    } });
  const running = engine.run([{ source_id: "secret-session-id", name: "IMG_1.JPG", media_type: "photo",
    size: 9, modified_at: 12, captured_at: "2026-01-01", camera: "Camera" }]);
  while (!releaseChunk) await new Promise((resolve) => setImmediate(resolve));
  engine.queue();
  releaseChunk();
  const result = await running;
  assert.equal(result.status, "queued");
  assert.equal(result.pendingFiles[0].name, "IMG_1.JPG");
  assert.equal(result.pendingFiles[0].source_id, undefined);
  assert.deepEqual(aborted, ["temporary"]);
});
