import type { AgentReply } from "./contracts";

const STORAGE_KEY = "yingxu.desktop.agent-conversations.v1";

export type AgentConversationMessage = {
  role: "assistant" | "user";
  text: string;
  reply?: AgentReply;
  actionId?: string;
};

export type AgentConversation = {
  id: string;
  title: string;
  updatedAt: string;
  messages: AgentConversationMessage[];
  unresolved?: boolean;
};

export function loadAgentConversations(storage: Pick<Storage, "getItem"> | null = getStorage()): AgentConversation[] {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return [];
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value.flatMap((item): AgentConversation[] => {
      if (!item || typeof item !== "object") return [];
      const row = item as Partial<AgentConversation>;
      if (typeof row.id !== "string" || typeof row.title !== "string" || typeof row.updatedAt !== "string" || !Array.isArray(row.messages)) return [];
      const messages = row.messages.flatMap((message): AgentConversationMessage[] => {
        if (!message || typeof message !== "object") return [];
        const candidate = message as Partial<AgentConversationMessage>;
        if ((candidate.role !== "assistant" && candidate.role !== "user") || typeof candidate.text !== "string") return [];
        return [{ role: candidate.role, text: candidate.text, ...(candidate.reply ? { reply: candidate.reply } : {}) }];
      });
      return [{ id: row.id, title: row.title, updatedAt: row.updatedAt, messages, ...(row.unresolved ? { unresolved: true } : {}) }];
    }).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  } catch {
    return [];
  }
}

export function saveAgentConversations(conversations: readonly AgentConversation[], storage: Pick<Storage, "setItem"> | null = getStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(conversations));
  } catch {
    // Keep the current conversation usable if local storage is unavailable or full.
  }
}

function getStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
