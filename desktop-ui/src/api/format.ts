import type { ArchiveTask, MediaFile, TaskState } from "./contracts";
export interface RawTarget {
  id: string;
  path: string;
  state: TaskState;
  copied: number;
  skipped: number;
  verified: number;
  failed: number;
}
export interface RawJob {
  id: string;
  name: string;
  state: TaskState;
  kind: string;
  verify?: boolean;
  createdAt: string;
  files: number;
  totalBytes: number;
  progress: number;
  phase: string;
  source: string;
  targets: RawTarget[];
  items?: { targetId: string; state: string; error?: string | null }[];
  error?: string | null;
  runtime?: { speedBytesPerSecond?: number };
}
export interface RawMedia {
  id: string;
  name: string;
  path: string;
  kind: MediaFile["kind"];
  bytes: number;
  qualityState: MediaFile["qualityState"];
  metadata?: Record<string, unknown>;
}
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.min(
    Math.floor(Math.log(value) / Math.log(1024)),
    units.length - 1,
  );
  return `${(value / 1024 ** i).toLocaleString("zh-CN", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}
export function formatTime(value: string): string {
  const d = new Date(value);
  return Number.isNaN(d.valueOf())
    ? value
    : d.toLocaleString("zh-CN", { hour12: false });
}
const phases: Record<string, string> = {
  queued: "排队中",
  copying: "正在复制",
  verifying: "正在校验",
  committing: "提交副本",
  success: "已完成",
  partial: "部分完成",
  failed: "失败",
  paused: "已暂停",
  cancelled: "已取消",
  user_pause: "已暂停",
  user_resume: "等待继续",
  user_cancel: "已取消",
  user_retry: "等待重试",
  interrupted_resume_required: "中断后等待继续",
  awaiting_source_removal_confirmation: "副本已校验，来源待再次确认",
  copied_without_verification: "已复制，未校验",
};
export function mapJob(j: RawJob): ArchiveTask {
  return {
    kind: j.kind,
    verify: j.verify,
    id: j.id,
    name: j.name,
    state: j.state,
    createdAt: formatTime(j.createdAt),
    files: j.files,
    size: formatBytes(j.totalBytes),
    progress: Math.round(j.progress * 10) / 10,
    speed:
      j.state === "running"
        ? `${formatBytes(j.runtime?.speedBytesPerSecond || 0)}/s`
        : "—",
    phase: phases[j.phase] || j.phase,
    source: j.source,
    error: j.error || undefined,
    destinations: j.targets.map((t) => ({
      id: t.id,
      name: t.path,
      state: t.state,
      progress: j.files
        ? Math.min(
            100,
            Math.round((100 * (t.copied + t.skipped + t.failed)) / j.files),
          )
        : j.progress,
      error:
        j.items?.find((i) => i.targetId === t.id && i.state === "failed")
          ?.error || undefined,
    })),
  };
}
export function mapMedia(f: RawMedia): MediaFile {
  const m = f.metadata || {};
  const review = m.humanReview as { state?: string } | undefined;
  const quality = m.quality as
    { status?: string; candidates?: string[] } | undefined;
  const labels: Record<string, string> = {
    highlight_clipping_candidate: "高光候选",
    deep_shadow_candidate: "暗部候选",
    low_detail_candidate: "低细节候选",
  };
  const qualityText = review?.state
    ? `人工复核：${review.state === "good" ? "通过" : review.state === "review" ? "待复核" : "未分析"}`
    : m.decodeStatus === "resource_limit"
      ? "受资源限制，未分析"
      : quality?.candidates?.length
        ? quality.candidates.map((v) => labels[v] || v).join("、") +
          " · 待人工复核"
        : f.qualityState === "review"
          ? "待人工复核"
          : m.decodeStatus === "decoded"
            ? "已解码 · 未校准"
            : m.decodeStatus === "frames_sampled"
              ? m.framesAnalyzed === 0 ? "抽帧信号不完整，待复核" : "已抽帧 · 待人工复核"
              : "未分析";
  const modified = (m.fingerprint as { modifiedNs?: number } | undefined)
    ?.modifiedNs;
  return {
    id: f.id,
    name: f.name,
    path: f.path,
    kind: f.kind,
    size: formatBytes(f.bytes),
    capturedAt:
      typeof m.capturedAt === "string"
        ? `${m.capturedAt}（EXIF）`
        : modified
          ? `${formatTime(new Date(modified / 1e6).toISOString())}（文件时间）`
          : "拍摄时间未知",
    device: typeof m.cameraModel === "string" ? m.cameraModel : "设备未知",
    location: "未读取地点",
    quality: qualityText,
    qualityState: f.qualityState,
  };
}
