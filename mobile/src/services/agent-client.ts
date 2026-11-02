import { secureStore } from "./media-access";

/**
 * 影序 Agent · 对话客户端（DeepSeek / OpenAI 兼容）
 *
 * 设计取舍：
 * - 对话走 DeepSeek（deepseek-chat，国内直连、便宜）；
 * - 审图这类视觉能力不放在这里——DeepSeek 没有视觉接口，交给工具层用视觉模型 key；
 * - 工具循环最多 4 轮，防模型反复调用把用户晾在那。
 */

const DEFAULT_BASE_URL = "https://api.deepseek.com/v1";
const DEFAULT_MODEL = "deepseek-chat";
const KEY_STORAGE = "yingxuAgentApiKeyV1";
const MAX_TOOL_STEPS = 4;

export type AgentRole = "system" | "user" | "assistant" | "tool";

export type AgentWireMessage = {
  role: AgentRole;
  content: string;
  tool_call_id?: string;
  tool_calls?: { id: string; type: string; function: { name: string; arguments: string } }[];
};

export type AgentToolNote = {
  name: string;
  args: string;
  result: string;
};

export type AgentTurnResult = {
  reply: string;
  toolNotes: AgentToolNote[];
};

export type AgentToolDef = {
  type: "function";
  function: { name: string; description: string; parameters: Record<string, unknown> };
};

export async function loadAgentKey(): Promise<string | null> {
  const value = await secureStore.get(KEY_STORAGE);
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export async function saveAgentKey(value: string): Promise<void> {
  const trimmed = value.trim();
  if (trimmed) {
    await secureStore.set(KEY_STORAGE, trimmed);
  } else {
    await secureStore.remove(KEY_STORAGE);
  }
}

const SYSTEM_PROMPT = [
  "你是影序 Pocket 的助手，运行在用户的手机上，帮摄影师处理素材备份与审片。",
  "用户询问手机或已连接电脑的备份状态、历史时，必须先调用对应的只读工具，不要凭空猜测。",
  "手机端只能读取已连接电脑的任务状态和历史，不能启动、暂停或取消电脑任务。",
  "只有用户明确要求审片时才调用审片工具；工具只能读取用户选中的照片。",
  "工具返回 ERROR 时，先读懂原因再决定是让用户补信息还是换方法。",
  "回答用中文，简短直接；涉及删除/改动数据的操作必须先让用户确认。",
].join("\n");

export async function runAgentTurn(options: {
  history: { role: "user" | "assistant"; text: string }[];
  userText: string;
  apiKey: string;
  tools: AgentToolDef[];
  executeTool: (name: string, argsJson: string) => Promise<string>;
  baseUrl?: string;
  model?: string;
  signal?: any;
}): Promise<AgentTurnResult> {
  const apiKey = options.apiKey.trim();
  if (!apiKey) throw new Error("还没有配置 Agent 的 API Key");

  const baseUrl = (options.baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
  const model = options.model || DEFAULT_MODEL;

  const messages: AgentWireMessage[] = [{ role: "system", content: SYSTEM_PROMPT }];
  for (const item of options.history.slice(-10)) {
    messages.push({ role: item.role, content: item.text });
  }
  messages.push({ role: "user", content: options.userText });

  const toolNotes: AgentToolNote[] = [];

  for (let step = 0; step < MAX_TOOL_STEPS; step += 1) {
    const response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      signal: options.signal,
      body: JSON.stringify({
        model,
        temperature: 0.3,
        messages,
        ...(options.tools.length > 0 ? { tools: options.tools, tool_choice: "auto" } : {}),
      }),
    });

    const rawText = await response.text();
    if (!response.ok) {
      throw new Error(extractErrorMessage(response.status, rawText));
    }

    let message: any;
    try {
      const parsed = JSON.parse(rawText);
      message = parsed?.choices?.[0]?.message;
    } catch {
      throw new Error("模型返回的不是合法 JSON");
    }
    if (!message) throw new Error("模型没有返回消息");

    const toolCalls: { id: string; type: string; function: { name: string; arguments: string } }[] =
      Array.isArray(message.tool_calls) ? message.tool_calls : [];

    if (toolCalls.length === 0) {
      const reply = String(message.content ?? "").trim();
      return { reply: reply || "（模型没有返回内容）", toolNotes };
    }

    // 把 assistant 的工具请求原样回填，再逐个执行
    messages.push({
      role: "assistant",
      content: String(message.content ?? ""),
      tool_calls: toolCalls,
    });

    for (const call of toolCalls) {
      const name = call?.function?.name ?? "unknown";
      const args = call?.function?.arguments ?? "{}";
      let result = "";
      try {
        result = await options.executeTool(name, args);
      } catch (cause) {
        result = `ERROR: ${cause instanceof Error ? cause.message : String(cause)}`;
      }
      toolNotes.push({ name, args, result });
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: result.slice(0, 4000),
      });
    }
  }

  return {
    reply: "这个任务我试了几步还没完成，先停在这里。你可以把需求拆小一点，或者告诉我更多信息。",
    toolNotes,
  };
}

function extractErrorMessage(status: number, rawText: string): string {
  try {
    const parsed = JSON.parse(rawText);
    const message = parsed?.error?.message ?? parsed?.message;
    if (typeof message === "string" && message.trim()) return `HTTP ${status}：${message.slice(0, 300)}`;
  } catch {
    // 忽略
  }
  return `HTTP ${status}：${rawText.slice(0, 200)}`;
}
