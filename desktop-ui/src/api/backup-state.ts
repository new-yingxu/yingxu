import type { ArchiveTask, TaskState } from "./contracts";

/**
 * 备份流程 UI 状态机（对齐 docs/桌面端/本地后端-API-v1.md 的 B1 契约）。
 * 只归纳 Rust 已有的 job state 与 runtime 字段，不发明接口字段。
 */

/**
 * 备份流程 UI 状态清单（交接文档定义）：
 * idle / scanning / preview(目标确认) / running(进度) / paused /
 * success / partial(部分成功) / failed / cancelled。
 * Rust job state 的 queued 在 UI 上归入 running，用 phase 文案区分"排队中"。
 */
export type BackupFlowState =
  | "idle"
  | "scanning"
  | "preview"
  | "running"
  | "paused"
  | "success"
  | "partial"
  | "failed"
  | "cancelled";

export type JobControlAction = "pause" | "resume" | "cancel" | "retry";

/**
 * 任务级控制按钮矩阵，对齐 B1 `jobs.control`：
 * pause|resume|cancel 用于活动任务；retry 只允许失败目标单目标重试。
 */
export const JOB_CONTROL: Record<
  TaskState,
  Record<JobControlAction, boolean>
> = {
  queued: { pause: true, resume: false, cancel: true, retry: false },
  running: { pause: true, resume: false, cancel: true, retry: false },
  paused: { pause: false, resume: true, cancel: true, retry: false },
  success: { pause: false, resume: false, cancel: false, retry: false },
  partial: { pause: false, resume: false, cancel: false, retry: true },
  cancelled: { pause: false, resume: false, cancel: false, retry: false },
  failed: { pause: false, resume: false, cancel: false, retry: true },
};

export function canControl(
  state: TaskState,
  action: JobControlAction,
): boolean {
  return JOB_CONTROL[state][action];
}

/** 单目标重试：仅任务为 partial/failed 且该目标 state 为 failed 时允许。 */
export function canRetryTarget(
  task: Pick<ArchiveTask, "state" | "destinations">,
  targetId: string,
): boolean {
  if (!JOB_CONTROL[task.state].retry) return false;
  return (
    task.destinations.find((d) => d.id === targetId)?.state === "failed"
  );
}

const JOB_TO_FLOW: Record<TaskState, BackupFlowState> = {
  queued: "running",
  running: "running",
  paused: "paused",
  success: "success",
  partial: "partial",
  cancelled: "cancelled",
  failed: "failed",
};

export function jobStateToFlow(state: TaskState): BackupFlowState {
  return JOB_TO_FLOW[state];
}

export function isTerminal(state: BackupFlowState): boolean {
  return (
    state === "success" ||
    state === "partial" ||
    state === "failed" ||
    state === "cancelled"
  );
}

export interface TaskResultSummary {
  state: BackupFlowState;
  headline: string;
  detail: string;
  succeededTargets: number;
  failedTargets: number;
  canRetryFailedTargets: boolean;
}

/** 终态结果摘要；非终态返回 null（进度区继续呈现 phase/速度/当前文件）。 */
export function summarizeResult(
  task: Pick<ArchiveTask, "state" | "destinations" | "error">,
): TaskResultSummary | null {
  const state = jobStateToFlow(task.state);
  switch (state) {
    case "idle":
    case "scanning":
    case "preview":
    case "running":
    case "paused":
      return null;
  }
  const succeeded = task.destinations.filter(
    (d) => d.state === "success",
  ).length;
  const failed = task.destinations.filter((d) => d.state === "failed").length;
  const total = task.destinations.length;
  const canRetry = failed > 0 && JOB_CONTROL[task.state].retry;
  const base: Record<
    Exclude<BackupFlowState, "idle" | "scanning" | "preview" | "running" | "paused">,
    string
  > = {
    success: "备份完成，所有目的地均已处理。",
    partial: `部分目的地完成：${succeeded}/${total} 个成功${failed ? `，${failed} 个失败` : ""}。`,
    failed: `任务失败：${failed || total} 个目的地未完成。`,
    cancelled: "任务已取消：已复制文件保留，未复制目的地不再继续。",
  };
  const hints: Record<string, string> = {
    success: "可在归档记录查看报告并导出。",
    partial: "失败目的地可单独重试，成功结果已保留。",
    failed: "失败目的地可重试；也可重新发起备份任务。",
    cancelled: "如需继续，请重新发起备份任务。",
  };
  return {
    state,
    headline: base[state],
    detail: hints[state],
    succeededTargets: succeeded,
    failedTargets: failed,
    canRetryFailedTargets: canRetry,
  };
}

export interface BackupWizardContext {
  scanned: boolean;
  targets: number;
  busy: boolean;
}

/** 备份页 4 步向导的步进守卫：未扫描不能进入后续步骤，无目标不能进入确认摘要。 */
export function canEnterStep(
  step: number,
  ctx: BackupWizardContext,
): boolean {
  if (ctx.busy) return false;
  if (step <= 1) return true;
  if (!ctx.scanned) return false;
  if (step >= 4) return ctx.targets > 0;
  return true;
}
