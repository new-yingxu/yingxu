// Executes the production adapter against the existing Rust CLI. Native picker is injected;
// this proves request mapping and real filesystem work, not native dialog acceptance.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import {
  mkdir,
  writeFile,
  readFile,
  realpath,
  readdir,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { createTauriApi, ApiError } from "../evidence/adapter-module/tauri.js";
const ui = await realpath(fileURLToPath(new URL("..", import.meta.url)));
const root = path.join(ui, "evidence", `adapter-fixture-${Date.now()}`);
const source = path.join(root, "source"),
  target = path.join(root, "target"),
  data = path.join(root, "db"),
  reports = path.join(root, "reports");
for (const p of [source, target, data, reports])
  await mkdir(p, { recursive: true });
const generated = spawnSync("/opt/homebrew/bin/ffmpeg", [
  "-v",
  "error",
  "-f",
  "lavfi",
  "-i",
  "testsrc=size=160x96:rate=10",
  "-t",
  "1",
  "-c:v",
  "libx264",
  "-pix_fmt",
  "yuv420p",
  path.join(source, "sample.mp4"),
]);
assert.equal(generated.status, 0, generated.stderr.toString());
const photo = spawnSync("/opt/homebrew/bin/ffmpeg", [
  "-v",
  "error",
  "-f",
  "lavfi",
  "-i",
  "color=white:size=64x48",
  "-frames:v",
  "1",
  path.join(source, "photo.png"),
]);
assert.equal(photo.status, 0, photo.stderr.toString());
await writeFile(path.join(source, "photo.xmp"), "synthetic fixture sidecar");
const binary = path.resolve(ui, "../backend/target/debug/yingxu-core");
const child = spawn(binary, ["--data-dir", data, "rpc"], {
  stdio: ["pipe", "pipe", "pipe"],
});
const waiters = [];
let errors = "";
child.stderr.on("data", (s) => (errors += s));
createInterface({ input: child.stdout }).on("line", (line) => {
  const waiter = waiters.shift();
  if (waiter)
    try {
      waiter.resolve(JSON.parse(line));
    } catch (error) {
      waiter.reject(error);
    }
});
child.on("exit", (code) => {
  while (waiters.length)
    waiters.shift().reject(new Error(`CLI exited ${code}: ${errors}`));
});
function raw(request) {
  return new Promise((resolve, reject) => {
    waiters.push({ resolve, reject });
    child.stdin.write(JSON.stringify(request) + "\n");
  });
}
const calls = [];
let destination = target;
let allow = true;
let dropStartReply = false;
let savedJobId;
async function invoke(command, args) {
  if (command === "select_directory")
    return {
      path: args.purpose === "source" ? source : destination,
      purpose: args.purpose,
    };
  assert.equal(command, "backend_request");
  calls.push(args.request);
  const reply = await raw(args.request);
  if (dropStartReply && args.request.method === "jobs.start" && reply.ok) {
    dropStartReply = false;
    savedJobId = reply.data.id;
    throw new Error("synthetic lost IPC reply after durable start");
  }
  return reply;
}
const confirmations = [];
const api = createTauriApi({
  invoke,
  confirm: async (value) => {
    confirmations.push(value);
    return allow;
  },
});
const results = [];
try {
  assert.equal((await api.local.health()).protocolVersion, 1);
  results.push("health protocol");
  const caps = await api.local.capabilities();
  assert.equal(caps.videoMetadata, true);
  assert.equal(caps.netdiskRealUpload, false);
  results.push("actual capabilities");
  await assert.rejects(
    api.auth.login("email-code", "fixture@example.invalid", "fixture"),
    (e) => e.code === "CLOUD_ADAPTER_NOT_CONNECTED",
  );
  results.push("cloud login fails closed");
  const scan = await api.backup.scan();
  assert.equal(scan.sources[0].files, 3);
  assert.equal(scan.sources[0].path, source);
  results.push("source selector and scan mapping");
  const indexed = await api.local.indexSource();
  assert.equal(indexed.files, 3);
  const media = await api.library.search("", "all", "all");
  assert.equal(media.length, 3);
  const png = media.find((f) => f.name === "photo.png"),
    video = media.find((f) => f.name === "sample.mp4");
  assert.ok(png && video);
  results.push("source index and formatted media");
  const firstPage = await api.library.searchPage("", "all", "all", 2, 0);
  assert.equal(firstPage.items.length, 2);
  assert.equal(firstPage.total, 3);
  assert.equal(firstPage.offset, 0);
  assert.equal(firstPage.limit, 2);
  assert.equal(firstPage.hasMore, true);
  const lastPage = await api.library.searchPage("", "all", "all", 2, 2);
  assert.equal(lastPage.items.length, 1);
  assert.equal(lastPage.offset, 2);
  assert.equal(lastPage.hasMore, false);
  await assert.rejects(
    api.library.searchPage("", "all", "all", 201, 0),
    (e) => e.code === "INVALID_PAGINATION",
  );
  results.push("paged library search against the real Rust CLI");
  const analyzed = await api.local.analyze(png.id, "photo");
  assert.equal(analyzed.thumbnailAvailable, true);
  assert.match(await api.local.thumbnail(png.id), /^data:image\/jpeg;base64,/);
  results.push("photo analysis and thumbnail");
  const motion = await api.local.analyze(video.id, "video");
  assert.equal(motion.framesUploaded ?? motion.metadata?.framesUploaded, false);
  results.push("video analysis stays local");
  const preset = (await api.presets.list()).find((p) => p.isDefault);
  assert.ok(preset);
  const payload = {
    sourceId: source,
    destinations: [target],
    presetId: preset.id,
    verify: true,
    nameValues: {
      date: "2026-10-26",
      event: "fixture-backup",
      device: "generated",
    },
  };
  allow = false;
  await assert.rejects(
    api.local.startBackup(payload),
    (e) => e.code === "USER_CANCELLED",
  );
  assert.equal((await api.history.list("", "all")).length, 0);
  assert.equal((await readdir(target)).length, 0);
  results.push("cancel confirmation creates no job or media copy");
  allow = true;
  dropStartReply = true;
  await assert.rejects(
    api.local.startBackup(payload),
    (e) => e.code === "IPC_UNAVAILABLE",
  );
  const queued = await api.local.startBackup(payload);
  assert.equal(queued.id, savedJobId);
  assert.equal((await api.history.list("", "all")).length, 1);
  assert.ok(confirmations.at(-1).rows.some((r) => r.value === source));
  results.push("lost reply replays same durable job");
  assert.equal((await api.backup.control(queued.id, "pause")).state, "paused");
  assert.equal((await api.backup.control(queued.id, "resume")).state, "queued");
  results.push("queue pause and resume");
  const worker = spawnSync(binary, ["--data-dir", data, "worker-drain"], {
    encoding: "utf8",
  });
  assert.equal(worker.status, 0, worker.stderr);
  const done = await api.history.detail(queued.id);
  assert.equal(done.state, "success");
  assert.equal(done.destinations[0].progress, 100);
  const summary = await api.local.report(done.id);
  assert.equal(summary.job.targets[0].verified, 3);
  const hash = (b) => createHash("sha256").update(b).digest("hex");
  for (const item of summary.job.items)
    assert.equal(
      hash(await readFile(path.join(source, item.relative))),
      hash(await readFile(item.output)),
    );
  results.push("real backup SHA256 and report");
  destination = reports;
  const exported = await api.local.exportReport(done.id, "json");
  assert.equal(JSON.parse(await readFile(exported, "utf8")).job.id, done.id);
  results.push("report export confirmation");
  const healthy=(await api.local.replicas()).find(r=>r.verified&&r.path.endsWith('photo.png'));
  assert.ok(healthy);destination=path.join(root,'restored');await mkdir(destination);
  await api.audit.restore(healthy.path,'');let restored=spawnSync(binary,['--data-dir',data,'worker-drain'],{encoding:'utf8'});assert.equal(restored.status,0,restored.stderr);assert.equal(hash(await readFile(path.join(destination,'photo.png'))),hash(await readFile(path.join(source,'photo.png'))));results.push('healthy replica restore through same adapter');
  destination=path.join(root,'delivery');await mkdir(destination);const pack=await api.delivery.create('联调交付',[png.id,video.id],true,true);const delivered=spawnSync(binary,['--data-dir',data,'worker-drain'],{encoding:'utf8'});assert.equal(delivered.status,0,delivered.stderr);assert.equal((await api.history.detail(pack.id)).state,'success');assert.equal((await readdir(destination)).length,1);results.push('verified delivery job through same adapter');
  const denied=createTauriApi({invoke:async(_command,args)=>({requestId:args.request.requestId,version:1,ok:false,error:{code:'RAW_PREVIEW_NOT_FOUND',message:'No embedded preview'}})});await assert.rejects(denied.local.analyze('fixture','photo'),e=>e.code==='RAW_PREVIEW_NOT_FOUND'&&e.message.includes('RAW_PREVIEW_NOT_FOUND'));results.push('backend error code retained');
  const disconnected=createTauriApi({invoke:async()=>{throw new Error('IPC transport disconnected')}});await assert.rejects(disconnected.local.health(),e=>e.code==='IPC_UNAVAILABLE');assert.equal(disconnected.mode,'native');results.push('native transport failure never falls back to mock');
  const broken = createTauriApi({
    invoke: async () => ({
      requestId: "wrong",
      version: 1,
      ok: true,
      data: {},
    }),
  });
  await assert.rejects(
    broken.local.health(),
    (e) => e.code === "INVALID_REPLY",
  );
  results.push("reply correlation rejects wrong request");
  await writeFile(
    path.join(root, "result.json"),
    JSON.stringify(
      {
        checks: results,
        passed: results.length,
        sourceFiles: 3,
        sourceRetained: true,
        transport:
          "production TS adapter -> injected picker -> real Rust JSON-lines",
        nativeIpcVerified: false,
        cloudVerified: false,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    JSON.stringify(
      {
        passed: results.length,
        checks: results,
        evidence: path.relative(ui, root),
      },
      null,
      2,
    ),
  );
} finally {
  child.stdin.end();
}
