import { reviewPhoto } from "./ai-review";
import type { AgentToolDef } from "./agent-client";

/**
 * Agent 可调用的工具。
 *
 * 工具拿到的是"当前选中素材"上下文（由界面传入），而不是让模型去猜 uri ——
 * 模型不知道手机里有哪些文件，让它编路径是必然出错的。
 */

export const AGENT_TOOL_DEFS: AgentToolDef[] = [
  {
    type: "function",
    function: {
      name: "get_mobile_backup_status",
      description: "读取手机当前或最近一次备份的状态、文件数量、校验数量和失败数量。用户询问手机备份进度或结果时调用。",
      parameters: { type: "object", properties: {}, required: [] },
    },
  },
  {
    type: "function",
    function: {
      name: "list_mobile_backup_history",
      description: "查询手机本机最近的备份历史。用户询问手机备份记录时调用；最多返回 10 条，不返回本机路径。",
      parameters: {
        type: "object",
        properties: { limit: { type: "integer", minimum: 1, maximum: 10 } },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_connected_desktop_status",
      description: "只读查询已配对电脑的当前任务状态。电脑未连接时会返回错误；手机不能通过此工具启动、暂停或取消电脑任务。",
      parameters: { type: "object", properties: {}, required: [] },
    },
  },
  {
    type: "function",
    function: {
      name: "list_connected_desktop_history",
      description: "只读查询已配对电脑最近的备份历史。最多返回 10 条，不返回电脑本机路径。",
      parameters: {
        type: "object",
        properties: { limit: { type: "integer", minimum: 1, maximum: 10 } },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_selected_photo",
      description: "查看用户当前选中的照片信息（文件名、大小）。用户说「这张」「当前这张」时先调它确认上下文。",
      parameters: { type: "object", properties: {}, required: [] },
    },
  },
  {
    type: "function",
    function: {
      name: "review_selected_photo",
      description:
        "对当前选中的照片做 AI 审片：返回可留用/需注意/建议重拍的判断、0-100 分数、问题列表与改进建议。用户要求「审一下这张/看看拍得怎么样」时调用。",
      parameters: { type: "object", properties: {}, required: [] },
    },
  },
];

export type AgentToolContext = {
  selectedPhoto?: { uri: string; name: string; size?: number } | null;
  /** 视觉模型 key（审图用）；Agent 对话本身的 key 是另一把 */
  visionApiKey?: string | null;
  mobileTask?: Record<string, any> | null;
  mobileHistory?: Record<string, any>[];
  desktopConnected?: boolean;
  desktopTask?: Record<string, any> | null;
  desktopHistory?: Record<string, any>[];
};

function count(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? Math.max(0, number) : 0;
}

function historySummary(item: Record<string, any>) {
  return {
    eventName: String(item.eventName || item.sourceName || "手机备份"),
    status: String(item.status || "未知"),
    startedAt: String(item.startedAt || "未知"),
    processedFiles: count(item.processedFiles),
    totalFiles: count(item.totalFiles),
    verifiedFiles: count(item.verifiedFiles),
    failedFiles: count(item.failedFiles),
    destinationName: String(item.destinationName || item.targetName || "未记录"),
  };
}

function taskSummary(task: Record<string, any> | null | undefined) {
  if (!task) return null;
  const progress = task.progress && typeof task.progress === "object" ? task.progress : {};
  return {
    status: String(task.status || "未知"),
    phase: String(task.phase || progress.phase || "未知"),
    processedFiles: count(task.processedFiles ?? progress.completed_items),
    totalFiles: count(task.totalFiles ?? progress.total_items),
    verifiedFiles: count(task.verifiedFiles),
    failedFiles: count(task.failedFiles),
    currentFile: String(task.currentFile || ""),
    updatedAt: String(task.updatedAt || task.updated_at || ""),
  };
}

function requestedLimit(argsJson: string) {
  try {
    const args = JSON.parse(argsJson);
    const limit = Number(args?.limit ?? 5);
    return Number.isFinite(limit) ? Math.min(10, Math.max(1, Math.floor(limit))) : 5;
  } catch {
    return 5;
  }
}

export async function executeAgentTool(
  name: string,
  _argsJson: string,
  context: AgentToolContext,
): Promise<string> {
  if (name === "get_mobile_backup_status") {
    const task = taskSummary(context.mobileTask);
    return task ? JSON.stringify(task) : "当前没有手机备份任务。";
  }

  if (name === "list_mobile_backup_history") {
    const items = (context.mobileHistory || []).slice(0, requestedLimit(_argsJson)).map(historySummary);
    return JSON.stringify({ items });
  }

  if (name === "get_connected_desktop_status") {
    if (!context.desktopConnected) return "ERROR: 手机当前没有连接到已配对的电脑。";
    const task = taskSummary(context.desktopTask);
    return task ? JSON.stringify(task) : "已连接电脑当前没有可读取的任务状态。";
  }

  if (name === "list_connected_desktop_history") {
    if (!context.desktopConnected) return "ERROR: 手机当前没有连接到已配对的电脑。";
    const items = (context.desktopHistory || []).slice(0, requestedLimit(_argsJson)).map(historySummary);
    return JSON.stringify({ items });
  }

  if (name === "get_selected_photo") {
    if (!context.selectedPhoto) {
      return "ERROR: 用户当前没有选中照片。请先让他点「选择审片素材」。";
    }
    return JSON.stringify({
      name: context.selectedPhoto.name,
      size: context.selectedPhoto.size ?? null,
      note: "已选中，可直接调用 review_selected_photo 审片",
    });
  }

  if (name === "review_selected_photo") {
    if (!context.selectedPhoto) {
      return "ERROR: 用户当前没有选中照片，无法审片。";
    }
    if (!context.visionApiKey) {
      return "ERROR: 还没有配置视觉模型 API Key（审图用）。请让用户到「AI 审片」页右上角填写。";
    }
    const result = await reviewPhoto({
      uri: context.selectedPhoto.uri,
      apiKey: context.visionApiKey,
    });
    return JSON.stringify({
      文件名: context.selectedPhoto.name,
      判断: result.verdict === "keep" ? "可留用" : result.verdict === "reject" ? "建议重拍或删除" : "需注意",
      分数: result.score,
      画面: result.subject,
      问题: result.issues,
      建议: result.advice,
    });
  }

  return `ERROR: 未知工具 ${name}`;
}
