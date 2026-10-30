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
};

export async function executeAgentTool(
  name: string,
  _argsJson: string,
  context: AgentToolContext,
): Promise<string> {
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
