const test = require("node:test");
const assert = require("node:assert/strict");
const { MobileBackupEngine, filterAndSortMedia, mediaTypeForName, organizeMedia } = require("../src/services/mobile-backup.cjs");
const { publicMetadata } = require("../src/services/firebase-sync.cjs");
const { createMobileStore } = require("../src/services/mobile-store.cjs");

test("classifies common photo, RAW, and video files while ignoring non-media", () => {
  assert.equal(mediaTypeForName("IMG_0001.HEIC"), "photo");
  assert.equal(mediaTypeForName("DSC_1.ARW"), "photo");
  assert.equal(mediaTypeForName("clip.MOV"), "video");
  assert.equal(mediaTypeForName("notes.txt"), null);
});

test("builds safe date and media type destination paths", () => {
  const [photo, video] = organizeMedia([
    { name: "one.jpg", mediaType: "photo", modifiedAt: new Date(2026, 0, 2).getTime() },
    { name: "../two.mp4", mediaType: "video", modifiedAt: new Date(2026, 0, 2).getTime() },
  ], "跨年/活动");
  assert.match(photo.destinationRelativePath, /2026年01月02日\/跨年_活动\/手机拍摄\/照片及视频原片\/照片\/one\.jpg$/);
  assert.match(video.destinationRelativePath, /\/视频\/_two\.mp4$/);
  assert.ok(!photo.destinationRelativePath.includes("../"));
});

test("prefers EXIF capture time and falls back to file modification time", async () => {
  const engine = new MobileBackupEngine({ mediaAccess: { async scanMediaAsync() { return [
    { uri: "source/later.jpg", name: "later.jpg", modifiedAt: 100, capturedAt: 300 },
    { uri: "source/earlier.jpg", name: "earlier.jpg", modifiedAt: 200, capturedAt: 100 },
    { uri: "source/fallback.jpg", name: "fallback.jpg", modifiedAt: 200 },
  ]; } } });
  const files = await engine.scan("source://camera");
  assert.deepEqual(files.map((file) => file.name), ["earlier.jpg", "fallback.jpg", "later.jpg"]);
});

test("mobile preferences filter videos and control date folders", () => {
  const files = filterAndSortMedia([
    { name: "clip.mp4", modifiedAt: 2 },
    { name: "photo.jpg", modifiedAt: 1 },
  ], { includeVideos: false });
  assert.deepEqual(files.map((file) => file.name), ["photo.jpg"]);
  const [organized] = organizeMedia([{ name: "photo.jpg", mediaType: "photo", modifiedAt: 1 }], "活动", { includeDateFolder: false });
  assert.match(organized.destinationRelativePath, /^活动\/手机拍摄\//);
  assert.doesNotMatch(organized.destinationRelativePath, /\d{4}年\d{2}月\d{2}日/);
});

test("old archive import can retain its existing subfolder layout and record its mode", async () => {
  const engine = new MobileBackupEngine({ mediaAccess: {
    async scanMediaAsync() { return [{ uri: "source/event/day/IMG_1.JPG", name: "IMG_1.JPG", relativePath: "event/day/IMG_1.JPG", size: 10, modifiedAt: 1 }]; },
    async copyFileVerifiedAsync(_source, _target, relative) {
      assert.equal(relative, "重新整理/event/day/IMG_1.JPG");
      return { status: "copied", sha256: "b".repeat(64) };
    },
  } });
  const record = await engine.run({ sourceUri: "source://archive", destinationUri: "target://new", sourceKind: "archive",
    eventName: "重新整理", preferences: { preserveSourceStructure: true } });
  assert.equal(record.sourceKind, "archive");
  assert.equal(record.status, "completed");
});

test("duplicate targets are verified and counted as skipped", async () => {
  const engine = new MobileBackupEngine({ mediaAccess: {
    async scanMediaAsync() { return [{ uri: "source/a.jpg", name: "a.jpg", size: 10, modifiedAt: 1 }]; },
    async copyFileVerifiedAsync() { return { status: "skipped", sha256: "c".repeat(64), size: 10 }; },
  } });
  const record = await engine.run({ sourceUri: "source://camera", destinationUri: "target://backup" });
  assert.equal(record.status, "completed");
  assert.equal(record.skippedFiles, 1);
  assert.equal(record.verifiedFiles, 1);
  assert.equal(record.files[0].status, "skipped");
});

test("revoked permissions, missing sources, full targets, and hash failures remain visible as partial failures", async () => {
  for (const message of ["系统文件授权已撤销", "来源文件不存在", "目标存储空间不足", "SHA-256 校验失败"]) {
    const engine = new MobileBackupEngine({ mediaAccess: {
      async scanMediaAsync() { return [{ uri: "source/a.jpg", name: "a.jpg", size: 10, modifiedAt: 1 }]; },
      async copyFileVerifiedAsync() { throw new Error(message); },
    } });
    const record = await engine.run({ sourceUri: "source://camera", destinationUri: "target://backup" });
    assert.equal(record.status, "partial", message);
    assert.equal(record.failedFiles, 1, message);
    assert.equal(record.files[0].status, "partial", message);
    assert.match(record.files[0].targets[0].error, new RegExp(message));
  }
});

test("cancel stops after the in-flight file and records the remaining work", async () => {
  let engine;
  engine = new MobileBackupEngine({ mediaAccess: {
    async scanMediaAsync() { return [
      { uri: "source/a.jpg", name: "a.jpg", size: 10, modifiedAt: 1 },
      { uri: "source/b.jpg", name: "b.jpg", size: 10, modifiedAt: 2 },
    ]; },
    async copyFileVerifiedAsync() { return { status: "copied", sha256: "d".repeat(64), size: 10 }; },
  }, onUpdate(task) { if (task.processedFiles === 1) engine.cancel(); } });
  const record = await engine.run({ sourceUri: "source://camera", destinationUri: "target://backup" });
  assert.equal(record.status, "cancelled");
  assert.equal(record.processedFiles, 1);
  assert.equal(record.totalFiles, 2);
  assert.equal(record.copiedFiles, 1);
});

test("copies files, tracks verification, and preserves partial records", async () => {
  const updates = [];
  const engine = new MobileBackupEngine({
    mediaAccess: {
      async scanMediaAsync() {
        return [
          { uri: "source/photo.jpg", name: "photo.jpg", size: 12, modifiedAt: 1 },
          { uri: "source/clip.mov", name: "clip.mov", size: 24, modifiedAt: 2 },
        ];
      },
      async copyFileVerifiedAsync(source, target, relative) {
        assert.equal(target, "target://backup");
        if (source.endsWith("clip.mov")) throw new Error("目标盘已断开");
        assert.match(relative, /照片\/photo\.jpg$/);
        return { status: "copied", sha256: "a".repeat(64) };
      },
    },
    onUpdate: (value) => updates.push(value),
    now: (() => { let index = 0; return () => `time-${++index}`; })(),
  });
  const record = await engine.run({ sourceUri: "source://SD", destinationUri: "target://backup", eventName: "测试" });
  assert.equal(record.status, "partial");
  assert.equal(record.copiedFiles, 1);
  assert.equal(record.verifiedFiles, 1);
  assert.equal(record.failedFiles, 1);
  assert.equal(record.processedFiles, 2);
  assert.ok(updates.some((item) => item.status === "copying"));
});

test("queues desktop-target work offline without marking files failed", async () => {
  const engine = new MobileBackupEngine({ mediaAccess: {
    async scanMediaAsync() { return [{ uri: "source/a.jpg", name: "a.jpg", size: 10, modifiedAt: 1 }]; },
    async copyFileVerifiedAsync() { const error = new Error("offline"); error.code = "DESKTOP_OFFLINE"; throw error; },
  } });
  const record = await engine.run({ sourceUri: "source://camera", destinationUri: "yingxu-desktop://inbox" });
  assert.equal(record.status, "queued");
  assert.equal(record.failedFiles, 0);
  assert.equal(record.processedFiles, 1);
  assert.equal(record.files[0].status, "queued");
});

test("continues local targets while desktop is offline and completes queued copies after reconnect", async () => {
  let desktopOnline = false;
  const localCopies = new Set();
  const desktopCopies = new Set();
  let desktopAttempts = 0;
  const engine = new MobileBackupEngine({ mediaAccess: {
    async scanMediaAsync() { return [
      { uri: "source/a.jpg", name: "a.jpg", size: 10, modifiedAt: 1 },
      { uri: "source/b.jpg", name: "b.jpg", size: 20, modifiedAt: 2 },
    ]; },
    async copyFileVerifiedAsync(source, target) {
      const name = source.split("/").pop();
      const copies = target === "yingxu-desktop://inbox" ? desktopCopies : localCopies;
      if (target === "yingxu-desktop://inbox") {
        desktopAttempts += 1;
        if (!desktopOnline) { const error = new Error("offline"); error.code = "DESKTOP_OFFLINE"; throw error; }
      }
      const status = copies.has(name) ? "skipped" : "copied";
      copies.add(name);
      return { status, sha256: `${name}-digest` };
    },
  } });
  const options = { sourceUri: "source://camera", destinationUri: "yingxu-desktop://inbox",
    destinationName: "影序电脑", destinationTargets: [
      { uri: "yingxu-desktop://inbox", name: "影序电脑" },
      { uri: "phone://backup", name: "手机存储" },
    ] };

  const queued = await engine.run(options);
  assert.equal(queued.status, "queued");
  assert.equal(queued.processedFiles, 2);
  assert.equal(queued.copiedFiles, 2);
  assert.equal(queued.verifiedFiles, 0);
  assert.equal(queued.failedFiles, 0);
  assert.equal(localCopies.size, 2);
  assert.equal(desktopAttempts, 1, "after the first offline error, don't retry the same desktop for every file");
  assert.equal(queued.files[0].targets[0].status, "queued");
  assert.equal(queued.files[0].targets[1].status, "copied");
  assert.equal(queued.files[1].targets[1].status, "copied");

  desktopOnline = true;
  const resumed = await engine.run(options);
  assert.equal(resumed.status, "completed");
  assert.equal(resumed.processedFiles, 2);
  assert.equal(resumed.copiedFiles, 2);
  assert.equal(resumed.skippedFiles, 0);
  assert.equal(resumed.verifiedFiles, 2);
  assert.equal(desktopCopies.size, 2);
  assert.ok(resumed.files.every((file) => file.targets.every((target) => ["copied", "skipped"].includes(target.status))));
});

test("cloud metadata includes file names and counts but excludes local paths and credentials", () => {
  const metadata = publicMetadata({
    id: "backup-1", sourcePath: "/Volumes/PRIVATE/DCIM", destinationPath: "/Users/me/Photos",
    token: "never-share", eventName: "旅行", totalFiles: 1, files: [
      { name: "IMG_1.JPG", size: 44, status: "verified", sourceUri: "file:///secret", mediaType: "photo" },
    ],
  });
  assert.equal(metadata.files[0].name, "IMG_1.JPG");
  assert.equal(metadata.files[0].size, 44);
  assert.ok(!JSON.stringify(metadata).includes("PRIVATE"));
  assert.ok(!JSON.stringify(metadata).includes("secret"));
  assert.ok(!JSON.stringify(metadata).includes("never-share"));
});

test("local history and resumable task survive store recreation", async () => {
  const values = new Map();
  const storage = {
    async getItem(key) { return values.get(key) ?? null; },
    async setItem(key, value) { values.set(key, value); },
    async removeItem(key) { values.delete(key); },
  };
  const first = createMobileStore(storage);
  const record = { id: "backup-1", status: "completed", files: [{ name: "IMG_1.JPG" }] };
  await first.saveHistory(record);
  await first.saveActiveTask({ id: "queued-1", status: "queued" });
  await first.savePreferences({ includeVideos: false });
  const reopened = createMobileStore(storage);
  assert.deepEqual(await reopened.getHistory(), [record]);
  assert.deepEqual(await reopened.getActiveTask(), { id: "queued-1", status: "queued" });
  assert.deepEqual(await reopened.getPreferences(), { sortByCaptureTime: true, includeDateFolder: true, includeVideos: false, preserveSourceStructure: false });
});

test("cloud offline outbox keeps only sanitized metadata and deduplicates history records", async () => {
  const values = new Map();
  const storage = { async getItem(key) { return values.get(key) ?? null; }, async setItem(key, value) { values.set(key, value); }, async removeItem(key) { values.delete(key); } };
  const store = createMobileStore(storage);
  const record = { id: "backup-1", status: "completed", sourcePath: "/private/DCIM", token: "do-not-sync",
    files: [{ name: "folder/IMG_1.JPG", sourceUri: "file:///private/DCIM/IMG_1.JPG", size: 10, status: "copied" }] };
  await store.queueCloudProgress(record);
  await store.queueCloudHistory(record);
  await store.queueCloudHistory({ ...record, status: "partial" });
  const outbox = await store.getCloudOutbox();
  assert.equal(outbox.progress.files[0].name, "IMG_1.JPG");
  assert.equal(outbox.history.length, 1);
  assert.doesNotMatch(JSON.stringify(outbox), /private|do-not-sync|sourceUri/);
  await store.clearCloudProgress();
  await store.clearCloudHistory("backup-1");
  assert.deepEqual(await store.getCloudOutbox(), { progress: null, history: [] });
});
