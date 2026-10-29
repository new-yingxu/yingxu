import type { ModelProviderConfig, NamePreset } from "./contracts";

export function agentModelSelection(config: ModelProviderConfig | null, localMode: boolean): string {
  if (!config) return "";
  if (localMode && config.configured && config.provider !== "ollama") return "";
  return config.configured && config.provider !== "ollama"
    ? `cloud:${config.provider}:${config.model}`
    : `local:${config.localModel || config.model}`;
}

export function agentModelDisplayLabel(config: ModelProviderConfig | null, localMode: boolean): string {
  if (!config) return "读取模型…";
  if (localMode && config.configured && config.provider !== "ollama") return "本机模式 · 请选择 Ollama";
  if (config.configured && config.provider !== "ollama") {
    return `云端 · ${config.provider === "deepseek" ? "DeepSeek" : "通义千问"} · ${config.model}`;
  }
  return `本机 · ${config.localModel || config.model}`;
}

export function canSendAgent(localMode: boolean, config: ModelProviderConfig | null): boolean {
  return !localMode || !!config?.configured && config.provider === "ollama";
}

export function presetDisplayName(preset: NamePreset, index: number, all: NamePreset[]): string {
  const name = preset.name.trim();
  const suffix = `#${index + 1}`;
  const duplicate = name && all.filter((item) => item.name.trim() === name).length > 1;
  if (name && name !== "新建预设" && !duplicate) return name;
  if (duplicate) return `${name} · ${suffix}`;
  return `未命名预设 · ${suffix}`;
}

export function friendlyUiError(error: unknown): string | null {
  const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  if (code === "USER_CANCELLED") {
    const message = error instanceof Error ? error.message : "";
    return message.includes("目录选择") ? "已取消目录选择，尚未开始扫描。" : "已取消操作，未执行更改。";
  }
  if (code === "REPORT_NOT_READY") return "当前任务尚未生成报告，请在任务完成后再查看。";
  return null;
}
