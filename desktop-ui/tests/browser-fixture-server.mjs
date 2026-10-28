// Local, token-guarded disposable test bridge. Not bundled or used by production code.
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { randomBytes } from "node:crypto";
import { mkdir, realpath, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const ui = await realpath(fileURLToPath(new URL("..", import.meta.url)));
const root = path.join(ui, "evidence", `ui-fixture-${Date.now()}`);
for (const name of ["source", "target", "db", "reports"])
  await mkdir(path.join(root, name), { recursive: true });
const source = path.join(root, "source"),
  target = path.join(root, "target"),
  data = path.join(root, "db");
for (const [name, input, extra] of [
  ["photo.png", "color=white:size=128x96", ["-frames:v", "1"]],
  [
    "sample.mp4",
    "testsrc=size=160x96:rate=10",
    ["-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
  ],
]) {
  const f = spawnSync("/opt/homebrew/bin/ffmpeg", [
    "-v",
    "error",
    "-f",
    "lavfi",
    "-i",
    input,
    ...extra,
    path.join(source, name),
  ]);
  if (f.status !== 0) throw new Error(f.stderr.toString());
}
await writeFile(path.join(source, "photo.xmp"), "synthetic UI sidecar");
const binary = path.resolve(ui, "../backend/target/debug/yingxu-core");
const child = spawn(binary, ["--data-dir", data, "rpc"], {
  stdio: ["pipe", "pipe", "inherit"],
});
const waits = [];
createInterface({ input: child.stdout }).on("line", (line) =>
  waits.shift()?.(JSON.parse(line)),
);
const methods = new Set([
  "auth.restore",
  "health",
  "capabilities.get",
  "workbench.snapshot",
  "settings.get",
  "presets.list",
  "presets.preview",
  "library.search",
  "library.index",
  "library.analyze",
  "library.thumbnail",
  "video.analyze",
  "backup.scan",
  "backup.preview",
  "jobs.start",
  "jobs.detail",
  "jobs.control",
  "history.list",
  "reports.get",
  "reports.export",
  "account.devices",
]);
function rpc(request) {
  return new Promise((resolve) => {
    waits.push(resolve);
    child.stdin.write(JSON.stringify(request) + "\n");
  });
}
const token = randomBytes(24).toString("hex");
let worker = null;
const server = http.createServer(async (req, res) => {
  const origin = req.headers.origin;
  if (origin !== "http://127.0.0.1:5173") {
    res.writeHead(403).end();
    return;
  }
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Vary", "Origin");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type,X-Fixture-Token");
  res.setHeader("Access-Control-Allow-Methods", "POST,OPTIONS");
  if (req.method === "OPTIONS") {
    res.writeHead(204).end();
    return;
  }
  if (
    req.method !== "POST" ||
    req.url !== "/invoke" ||
    req.headers["x-fixture-token"] !== token
  ) {
    res.writeHead(403).end();
    return;
  }
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1024 * 1024) {
      res.writeHead(413).end();
      return;
    }
  }
  try {
    const { command, args } = JSON.parse(raw);
    let result;
    if (command === "select_directory") {
      if (!["source", "destination"].includes(args?.purpose))
        throw new Error("Purpose");
      result = {
        path: args.purpose === "source" ? source : target,
        purpose: args.purpose,
      };
    } else if (command === "backend_request") {
      const request = args.request;
      if (!methods.has(request.method))
        throw new Error("Method unavailable in fixture bridge");
      const p = request.params || {};
      if (p.source && p.source !== source)
        throw new Error("Source outside fixture");
      if (p.destinations && p.destinations.some((t) => t !== target))
        throw new Error("Target outside fixture");
      if (p.destination && p.destination !== target)
        throw new Error("Export outside fixture");
      result = await rpc(request);
      if (request.method === "jobs.start" && result.ok && !worker) {
        worker = spawn(binary, ["--data-dir", data, "worker-drain"], {
          stdio: "ignore",
        });
        worker.on("exit", () => (worker = null));
      }
    } else throw new Error("Command unavailable");
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(result));
  } catch (error) {
    res
      .writeHead(400, { "Content-Type": "application/json" })
      .end(JSON.stringify({ error: "Invalid fixture request" }));
  }
});
server.listen(5184, "127.0.0.1", async () => {
  const url = `http://127.0.0.1:5173/tests/browser-harness.html?token=${token}`;
  await writeFile(
    path.join(ui, "evidence/browser-fixture-session.json"),
    JSON.stringify({ url, fixture: root, pid: process.pid }, null, 2),
  );
  console.log(
    "Disposable fixture bridge ready on loopback; session URL stored locally.",
  );
});
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => {
    server.close();
    child.stdin.end();
    if (worker) worker.kill("SIGTERM");
    process.exit(0);
  });
