import { invoke as nativeInvoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  AppApi,
  AppSettings,
  BackupPayload,
  Confirmation,
  LocalCapabilities,
  NamePreset,
  ScanReport, IndexResult, NetdiskProvider, NetdiskTask,
} from "./contracts";
import { formatBytes, formatTime, mapJob, mapMedia } from "./format";
import type { RawJob, RawMedia } from "./format";
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public method?: string,
  ) {
    super(`[${code}] ${message}`);
    this.name = "ApiError";
  }
}
export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "code" in error)
    return `[${String(error.code)}] ${"message" in error ? String(error.message) : "请求失败"}`;
  return String(error);
}
export type Invoke = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;
interface Options {
  invoke?: Invoke;
  listen?: typeof listen;
  confirm?: (value: Confirmation) => Promise<boolean>;
}
interface RawPreset {
  id: string;
  name: string;
  fields: string[];
  isDefault: boolean;
  version: number;
}
interface Plan {
  id: string;
  request: {
    source: string;
    destinations: string[];
    subdirectory?: string | null;
    verify: boolean;
  };
  files: { relative: string }[];
  totalBytes: number;
  availableBytes: number[];
}
const fieldLabels: Record<string, string> = {
  date: "日期",
  event: "事件",
  location: "地点",
  device: "设备",
  kind: "类型",
  custom: "自定义文本",
};
function mapPreset(p: RawPreset): NamePreset {
  return {
    id: p.id,
    name: p.name,
    isDefault: p.isDefault,
    pattern: p.fields.map((f) => fieldLabels[f] || f).join(" / "),
  };
}
export function createTauriApi(options: Options = {}): AppApi {
  const invoke = options.invoke || nativeInvoke;
  const confirm = options.confirm || (() => Promise.resolve(false));
  const presetCache = new Map<string, RawPreset>();
  const transferSelections = new Map<
    string,
    { source: string; relative: string }
  >();
  const pendingStarts = new Map<string, { plan: Plan; key: string }>();
  const writeKeys = new Map<string, string>();
  let counter = 0;
  let lastIndex: IndexResult | null = null;
  const eventListen = options.listen || listen;
  async function rpc<T>(method: string, params: unknown = {}): Promise<T> {
    const requestId = `desktop-${Date.now()}-${++counter}`;
    let reply: {
      requestId: string;
      version: number;
      ok: boolean;
      data?: T;
      error?: { code: string; message: string };
    };
    try {
      reply = await invoke("backend_request", {
        request: { requestId, version: 1, method, params },
      });
    } catch (e) {
      if (e instanceof ApiError) throw e;
      const code =
        e && typeof e === "object" && "code" in e
          ? String(e.code)
          : "IPC_UNAVAILABLE";
      throw new ApiError(code, errorText(e), method);
    }
    if (
      reply.requestId !== requestId ||
      reply.version !== 1 ||
      typeof reply.ok !== "boolean"
    )
      throw new ApiError("INVALID_REPLY", "后端响应关联或协议无效", method);
    if (!reply.ok)
      throw new ApiError(
        reply.error?.code || "BACKEND_ERROR",
        reply.error?.message || "请求失败",
        method,
      );
    return reply.data as T;
  }
  async function durableWrite<T>(
    method: string,
    params: Record<string, unknown>,
  ): Promise<T> {
    const identity = JSON.stringify({ method, params });
    let key = writeKeys.get(identity);
    if (!key) {
      key = crypto.randomUUID();
      writeKeys.set(identity, key);
    }
    const result = await rpc<T>(method, { ...params, idempotencyKey: key });
    writeKeys.delete(identity);
    return result;
  }
  async function chooseDirectory(
    purpose: "source" | "destination",
  ): Promise<string> {
    let selection: { path: string; purpose: string } | null;
    try {
      selection = await invoke("select_directory", { purpose });
    } catch (e) {
      throw new ApiError("DIRECTORY_SELECTION_FAILED", errorText(e));
    }
    if (!selection) throw new ApiError("USER_CANCELLED", "已取消目录选择");
    return selection.path;
  }
  async function consent(data: Confirmation) {
    if (!(await confirm(data)))
      throw new ApiError("USER_CANCELLED", "操作已取消，未启动媒体文件写入");
  }
  async function listPresets() {
    const rows = await rpc<RawPreset[]>("presets.list");
    presetCache.clear();
    rows.forEach((p) => presetCache.set(p.id, p));
    return rows.map(mapPreset);
  }
  async function launch(payload: BackupPayload, transfer = false) {
    const fingerprint = JSON.stringify({ payload, transfer });
    let pending = pendingStarts.get(fingerprint);
    if (!pending) {
      if (!payload.destinations.length)
        throw new ApiError("DESTINATION_REQUIRED", "请先选择本地目的地");
      if (
        payload.destinations.some((p) =>
          ["local", "baidu", "quark"].includes(p),
        )
      )
        throw new ApiError(
          "DIRECTORY_GRANT_REQUIRED",
          "请选择原生目录；网盘不能代替本地备份目标",
        );
      let subdirectory: string | null = null;
      if (payload.presetId) {
        if (!presetCache.size) await listPresets();
        const preset = presetCache.get(payload.presetId);
        if (!preset) throw new ApiError("PRESET_NOT_FOUND", "请选择有效预设");
        subdirectory = await rpc<string>("presets.preview", {
          presetId: preset.id,
          values: payload.nameValues || {},
        });
      }
      const plan = await rpc<Plan>(
        transfer ? "transfer.preview" : "backup.preview",
        {
          source: payload.sourceId,
          destinations: payload.destinations,
          name:
            payload.nameValues?.event ||
            (transfer ? "转移老内容" : "复制新内容"),
          verify: transfer || payload.verify,
          selected: transfer
            ? Array.from(transferSelections.entries())
                .filter(([id]) => payload.nameValues?.[id] === "selected")
                .map(([, v]) => v.relative)
            : [],
          subdirectory,
        },
      );
      pending = { plan, key: `start-${plan.id}` };
      pendingStarts.set(fingerprint, pending);
    }
    const { plan, key } = pending;
    try {
      await consent({
        title: transfer ? "确认复制并校验转移副本" : "确认本地备份",
        description: transfer
          ? "只复制并校验。来源暂存必须完成后再次确认。"
          : "确认后将真实写入以下本地目录，同名文件不会被覆盖。",
        rows: [
          { label: "来源", value: plan.request.source },
          { label: "目的地", value: plan.request.destinations.join("\n") },
          {
            label: "命名子目录",
            value: plan.request.subdirectory || "保留来源相对目录",
          },
          {
            label: "容量",
            value: `${plan.files.length} 个文件 · ${formatBytes(plan.totalBytes)}`,
          },
          {
            label: "空闲空间",
            value: plan.availableBytes.map(formatBytes).join(" / "),
          },
          {
            label: "校验",
            value: plan.request.verify
              ? "SHA-256"
              : "跳过目标校验（报告会标记未校验）",
          },
        ],
        files: plan.files.slice(0, 20).map((f) => f.relative),
        confirmLabel: "确认并开始真实写入",
      });
    } catch (e) {
      pendingStarts.delete(fingerprint);
      throw e;
    }
    try {
      const job = await rpc<RawJob>("jobs.start", {
        planId: plan.id,
        confirmed: true,
        idempotencyKey: key,
      });
      pendingStarts.delete(fingerprint);
      return mapJob(job);
    } catch (e) {
      if (
        e instanceof ApiError &&
        ["PLAN_EXPIRED", "SOURCE_CHANGED", "TARGET_CHANGED"].includes(e.code)
      )
        pendingStarts.delete(fingerprint);
      throw e;
    }
  }
  async function approved(provider: string, authorized = false) {
    if (provider !== "baidu") throw new ApiError("NETDISK_NOT_APPROVED", "当前只开放百度个人网盘；模拟服务不作为真实网盘入口");
    const providers = await rpc<NetdiskProvider[]>("netdisk.providers");
    const row = providers.find(p => p.id === provider);
    if (!row || row.approval !== "approved" || !row.realUploadAvailable) throw new ApiError("NETDISK_NOT_APPROVED", "资质未获批或没有公开API", "netdisk.providers");
    if (authorized && !row.authorized) throw new ApiError("NETDISK_AUTH_REQUIRED", "请先完成该网盘授权", "netdisk.providers");
    return row;
  }
  async function uploadTask(taskId: string) {
    const rows = await rpc<NetdiskTask[]>("netdisk.tasks");
    const row = rows.find(t => t.taskId === taskId);
    if (!row) throw new ApiError("NETDISK_TASK_NOT_FOUND", "上传任务不存在");
    return row;
  }
  const api: AppApi = {
    mode: "native",
    auth: {
      restore: () => rpc("auth.restore"),
      login: () =>
        Promise.reject(
          new ApiError(
            "CLOUD_ADAPTER_NOT_CONNECTED",
            "真实登录尚未连接，不能创建登录会话",
          ),
        ),
      logout: () => rpc("auth.logout"),
    },
    workbench: {
      async snapshot() {
        const s = await rpc<{
          tasks: RawJob[];
          fileCount: number;
          totalBytes: number;
          reviewCount: number;
        }>("workbench.snapshot");
        return {
          tasks: s.tasks.map(mapJob),
          fileCount: s.fileCount,
          totalSize: formatBytes(s.totalBytes),
          reviewCount: s.reviewCount,
          destinations: new Set(
            s.tasks.flatMap((j) => j.targets.map((t) => t.path)),
          ).size,
        };
      },
    },
    backup: {
      async scan() {
        const source = await chooseDirectory("source");
        const report = await rpc<ScanReport & { files: { relative: string; fingerprint: { bytes: number } }[] }>("backup.scanReport", { source });
        const rows = report.files;
        return { scanReport: { skipped: report.skipped, scanned: report.scanned, skippedTotal: report.skippedTotal, skippedTruncated: report.skippedTruncated },
          sources: [
            {
              id: source,
              name: source.split(/[\\/]/).filter(Boolean).pop() || source,
              path: source,
              files: rows.length,
              size: formatBytes(
                rows.reduce((sum, f) => sum + f.fingerprint.bytes, 0),
              ),
              free: "目的地预检时读取",
            },
          ],
        };
      },
      start: (payload) => launch(payload),
      control: async (jobId, action) =>
        mapJob(
          await rpc<RawJob>("jobs.control", { jobId, action, targetId: null }),
        ),
    },
    transfer: {
      async inspect() {
        const source = await chooseDirectory("source");
        lastIndex = await rpc<IndexResult>("library.index", { source });
        const files = await rpc<RawMedia[]>("library.search", {
          query: source,
          kind: "all",
          quality: "all",
        });
        transferSelections.clear();
        const sourceBase = source.replace(/[\\/]$/, "");
        const result = files.filter(
          (f) =>
            f.path.startsWith(sourceBase + "/") ||
            f.path.startsWith(sourceBase + "\\"),
        );
        for (const f of result)
          transferSelections.set(f.id, {
            source,
            relative: f.path.slice(sourceBase.length + 1),
          });
        return result.map(mapMedia);
      },
      async start(ids, target, confirmed) {
        if (!confirmed)
          throw new ApiError(
            "CONFIRMATION_REQUIRED",
            "请确认理解转移的来源安全规则",
          );
        if (!ids.length)
          throw new ApiError("EMPTY_SELECTION", "请选择转移文件");
        const entries = ids.map((id) => transferSelections.get(id));
        if (
          entries.some((e) => !e) ||
          new Set(entries.map((e) => e!.source)).size !== 1
        )
          throw new ApiError("INVALID_SELECTION", "请重新扫描同一来源");
        return launch(
          {
            sourceId: entries[0]!.source,
            destinations: [target],
            presetId: "",
            verify: true,
            nameValues: Object.fromEntries(ids.map((id) => [id, "selected"])),
          },
          true,
        );
      },
    },
    library: {
      search: async (query, kind, quality) =>
        (await rpc<RawMedia[]>("library.search", { query, kind, quality })).map(
          mapMedia,
        ),
    },
    history: {
      list: async (query, state) =>
        (await rpc<RawJob[]>("history.list", { query, state })).map(mapJob),
      detail: async (jobId) =>
        mapJob(await rpc<RawJob>("jobs.detail", { jobId })),
      async rename(jobId, name) {
        await consent({
          title: "修改归档记录名称",
          description: "只改任务记录名称，不改媒体文件。",
          rows: [
            { label: "任务", value: jobId },
            { label: "新名称", value: name },
          ],
        });
        await rpc("history.rename", { jobId, name, confirmed: true });
      },
      async clear() {
        await consent({
          title: "清空归档记录",
          description: "不会删除媒体文件；活动任务存在时后端会拒绝。",
          rows: [{ label: "影响", value: "清空本机历史及报告记录" }],
        });
        await rpc("history.clear", { confirmation: "清空归档记录" });
      },
      retry: async (jobId, targetId) =>
        mapJob(
          await rpc<RawJob>("jobs.control", {
            jobId,
            action: "retry",
            targetId,
          }),
        ),
    },
    agent: {
      ask: () =>
        Promise.reject(
          new ApiError("CLOUD_ADAPTER_NOT_CONNECTED", "Agent 服务尚未连接"),
        ),
      confirm: () =>
        Promise.reject(
          new ApiError(
            "CLOUD_ADAPTER_NOT_CONNECTED",
            "Agent 本地执行桥尚未连接",
          ),
        ),
    },
    account: {
      devices: () => rpc("account.devices"),
      revoke: (deviceId) => rpc("account.revoke", { deviceId }),
      updateSync: () =>
        Promise.reject(
          new ApiError(
            "CLOUD_ADAPTER_NOT_CONNECTED",
            "云端同步偏好尚未连接；本机设置不能代表远端已更新",
          ),
        ),
    },
    settings: {
      get: () => rpc("settings.get"),
      update: (patch) => rpc("settings.update", patch),
      reset: () => rpc("settings.reset"),
      connectProvider: () =>
        Promise.reject(
          new ApiError(
            "AUTH_FLOW_REQUIRED",
            "请在个人网盘面板查看实时资质并完成官方授权",
          ),
        ),
    },
    presets: {
      list: listPresets,
      async save(p) {
        const reverse = Object.fromEntries(
          Object.entries(fieldLabels).map(([key, label]) => [label, key]),
        );
        const fields = p.pattern
          .split("/")
          .map((s) => s.trim())
          .map((s) => reverse[s] || s);
        const saved = await rpc<RawPreset>("presets.save", {
          id: p.id,
          name: p.name,
          fields,
          isDefault: p.isDefault,
          version: presetCache.get(p.id)?.version || 1,
        });
        presetCache.set(saved.id, saved);
        return mapPreset(saved);
      },
      remove: (id) => rpc("presets.remove", { id }),
    },
    audit: {
      run: () => rpc("audit.run"),
      async restore(path) {
        const replicas =
          await rpc<{ id: string; path: string; verified: boolean }[]>(
            "replicas.list",
          );
        const replica = replicas.find((r) => r.path === path && r.verified);
        if (!replica)
          throw new ApiError(
            "VERIFIED_REPLICA_REQUIRED",
            "请从审计登记的健康副本选择恢复来源",
          );
        const destination = await chooseDirectory("destination");
        await consent({
          title: "恢复健康副本",
          description: "重新验证副本，恢复到新位置；不覆盖已有文件。",
          rows: [
            { label: "副本", value: path },
            { label: "目标", value: destination },
          ],
        });
        const job = await durableWrite<RawJob>("restore.start", {
          replicaId: replica.id,
          destination,
          confirmed: true,
        });
        return `恢复任务 ${job.id}：${mapJob(job).phase}`;
      },
    },
    delivery: {
      async create(name, mediaIds, includeContactSheet, includeManifest) {
        const destination = await chooseDirectory("destination");
        await consent({
          title: "创建客户交付包",
          description: "校验后的副本写入独立目录；排队不代表完成。",
          rows: [
            { label: "名称", value: name },
            { label: "素材", value: mediaIds.join("\n") },
            { label: "目标", value: destination },
          ],
        });
        const job = await durableWrite<RawJob>("delivery.create", {
          name,
          mediaIds,
          destination,
          includeContactSheet,
          includeManifest,
          confirmed: true,
        });
        return {
          id: job.id,
          name: job.name,
          items: job.files,
          includeContactSheet,
          includeManifest,
          createdAt: `${formatTime(job.createdAt)} · ${mapJob(job).phase}`,
        };
      },
    },
    netdisk: {
      providers: () => rpc<NetdiskProvider[]>("netdisk.providers"),
      tasks: () => rpc<NetdiskTask[]>("netdisk.tasks"),
      async beginAuth(provider) {
        const p = await approved(provider);
        await consent({ title: "连接个人网盘", description: "将在系统浏览器授权，凭据由后端保存到系统安全存储；此操作不上传文件。", rows: [{ label: "网盘", value: p.display }] });
        const result = await rpc<{authorizeUrl: string; expiresInSeconds: number}>("netdisk.beginAuth", {provider});
        const url = new URL(result.authorizeUrl);
        if (provider !== "baidu" || url.protocol !== "https:" || url.hostname !== "openapi.baidu.com" || url.pathname !== "/oauth/2.0/authorize") throw new ApiError("INVALID_AUTH_URL", "授权地址不在允许的官方范围内");
        return result;
      },
      authPoll: provider => rpc("netdisk.authPoll", {provider}),
      async completeAuth(provider) { await approved(provider); await rpc("netdisk.completeAuth", {provider, code: ""}); },
      async enqueue(provider, file) {
        const p = await approved(provider, true);
        await consent({title:"加入网盘上传队列",description:"后端会验证本地副本SHA-256。排队不上传，上传还需另行确认。",rows:[{label:"网盘",value:p.display},{label:"文件",value:file.name},{label:"本地路径",value:file.path}]});
        await rpc("netdisk.enqueue",{provider,mediaId:file.id});
      },
      async run(taskId) {
        const task=await uploadTask(taskId), p=await approved(task.provider,true);
        await consent({title:"确认上传或续传",description:"确认后会真实向个人网盘发送此文件；以远端校验成功为完成依据。",rows:[{label:"网盘",value:p.display},{label:"文件",value:task.name},{label:"容量",value:formatBytes(task.bytes)},{label:"分片",value:`${task.chunksDone}/${task.chunksTotal}`}]});
        await rpc("netdisk.run",{taskId});
      },
      async cancel(taskId) {
        const task=await uploadTask(taskId);
        await consent({title:"停止上传任务",description:"停止本地任务；云端已经产生的内容以网盘实际状态为准。",rows:[{label:"文件",value:task.name}]});
        await rpc("netdisk.cancel",{taskId});
      },
      async unbind(provider) {
        await consent({title:"解除个人网盘授权",description:"删除本机安全存储中的授权，保留历史记录；不会删除网盘文件。",rows:[{label:"网盘",value:provider}]});
        await rpc("netdisk.unbind",{provider,confirmed:true});
      },
    },
    local: {
      replicas: () => rpc("replicas.list"),
      health: () => rpc("health"),
      capabilities: () => rpc<LocalCapabilities>("capabilities.get"),
      chooseDirectory,
      async indexSource() {
        const source = await chooseDirectory("source");
        lastIndex = await rpc<IndexResult>("library.index", { source });
        return lastIndex;
      },
      lastIndexReport: () => lastIndex,
      analyze: (mediaId, kind) =>
        rpc(
          kind === "video" ? "video.analyze" : "library.analyze",
          kind === "video" ? { mediaId, extractFrames: true } : { mediaId },
        ),
      async thumbnail(mediaId) {
        const data = await rpc<{ mimeType: string; bytes: number[] }>(
          "library.thumbnail",
          { mediaId },
        );
        if (data.mimeType !== "image/jpeg" || data.bytes.length > 1024 * 1024)
          throw new ApiError("INVALID_THUMBNAIL", "缩略图格式或大小无效");
        let binary = "";
        for (let i = 0; i < data.bytes.length; i += 8192)
          binary += String.fromCharCode(...data.bytes.slice(i, i + 8192));
        return `data:${data.mimeType};base64,${btoa(binary)}`;
      },
      report: (jobId) => rpc("reports.get", { jobId }),
      async exportReport(jobId, format) {
        const destination = await chooseDirectory("destination");
        await consent({
          title: "导出本地报告",
          description: "报告可能包含文件路径，仅导出到你选择的本地目录。",
          rows: [
            { label: "任务", value: jobId },
            { label: "目录", value: destination },
            { label: "格式", value: format },
          ],
        });
        return rpc("reports.export", {
          jobId,
          destination,
          format,
          confirmed: true,
        });
      },
      startBackup: (payload) => launch(payload),
      async confirmSourceRemoval(jobId) {
        const p = await rpc<{
          token: string;
          files: string[];
          effect: string;
          expiresAt: string;
        }>("transfer.prepareRemoval", { jobId });
        await consent({
          title: "再次确认来源可恢复暂存",
          description:
            "所有副本校验后，来源将移入同一设备的可恢复目录；仍占来源空间，不是永久删除。",
          rows: [
            { label: "任务", value: jobId },
            { label: "影响", value: p.effect },
            { label: "有效期", value: p.expiresAt },
          ],
          files: p.files,
          confirmLabel: "确认移入可恢复目录",
        });
        await rpc("transfer.confirmRemoval", {
          token: p.token,
          confirmation: "将来源移入可恢复目录",
        });
      },
      async subscribe(onJob, onError, onNotice) {
        if (options.invoke && !options.listen) return () => {};
        let trayNoticeShown = false;
        const stops = await Promise.all([
          eventListen<RawJob>("local-job-updated", (event) =>
            onJob(mapJob(event.payload)),
          ),
          eventListen("local-backend-error", (event) => onError(event.payload)),
          eventListen("local-minimized-to-tray", () => {
            if (!trayNoticeShown) { trayNoticeShown = true; onNotice?.("已缩到托盘/菜单栏，点击影序图标可恢复窗口。"); }
          }),
        ]);
        return () => stops.forEach((stop) => stop());
      },
    },
  };
  return api;
}
