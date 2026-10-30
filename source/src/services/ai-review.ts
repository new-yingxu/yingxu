import { mediaAccess, secureStore } from "./media-access";

/**
 * 影序 Pocket · AI 审片服务
 *
 * 设计要点（工期优先，别乱加依赖）：
 * - 读图/缩放/压缩全部交给原生 `readThumbnailBase64Async`，JS 层不碰二进制；
 * - 模型走 OpenAI 兼容协议，换供应商只改 baseUrl + model；
 * - API Key 存在原生 secureStore（Keystore/AES），不进 JS 明文存储、不进 bundle。
 */

export type ReviewVerdict = "keep" | "review" | "reject";

export type PhotoReview = {
  verdict: ReviewVerdict;
  score: number;
  subject: string;
  issues: string[];
  advice: string;
};

export type ReviewOptions = {
  uri: string;
  apiKey: string;
  baseUrl?: string;
  model?: string;
  /** 长边像素，默认 1024：够看清构图与清晰度，又能把上传压到几百 KB */
  maxDimension?: number;
  /** 取消用的 AbortSignal；RN 类型里不一定有该全局类型，故宽类型 */
  signal?: any;
};

const API_KEY_STORAGE = "yingxuAiApiKeyV1";
const DEFAULT_BASE_URL = "https://api.openai.com/v1";
const DEFAULT_MODEL = "gpt-6-luna";

const SYSTEM_PROMPT = [
  "你是影序的审片助手。影序是一款摄影素材备份与初步筛选工具，用户是摄影师。",
  "你的任务：看一张照片，给出简洁、可执行的审片意见。",
  "只输出一个 JSON 对象，不要 markdown 代码块，不要额外解释。",
].join("\n");

const USER_PROMPT = [
  "请审这张照片，严格按以下 JSON 结构输出：",
  '{"subject":"一句话描述画面","score":0到100的整数,"verdict":"keep|review|reject","issues":["问题1","问题2"],"advice":"一句可执行建议"}',
  "",
  "判断维度：清晰度（是否失焦/手抖模糊）、曝光（过曝/欠曝/死黑死白）、构图、主体是否可辨识。",
  "verdict 含义：keep=可留用；review=可留但需注意；reject=建议删除或重拍。",
  "issues 最多 3 条，没有问题时给空数组。",
].join("\n");

export async function reviewPhoto(options: ReviewOptions): Promise<PhotoReview> {
  const apiKey = options.apiKey.trim();
  if (!apiKey) throw new Error("还没有填写 API Key");

  const thumb = await mediaAccess.readThumbnailBase64Async(
    options.uri,
    options.maxDimension ?? 1024,
    72,
  );
  if (!thumb?.data) throw new Error("读取照片失败（可能不是可解码的图片格式）");

  const baseUrl = (options.baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
  const model = options.model || DEFAULT_MODEL;

  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    signal: options.signal,
    body: JSON.stringify({
      model,
      temperature: 0.2,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          content: [
            { type: "text", text: USER_PROMPT },
            {
              type: "image_url",
              // detail=high：审片要看清失焦与曝光，低清会误判
              image_url: { url: `data:${thumb.mimeType};base64,${thumb.data}`, detail: "high" },
            },
          ],
        },
      ],
    }),
  });

  const rawText = await response.text();
  if (!response.ok) {
    throw new Error(extractErrorMessage(response.status, rawText));
  }

  let content = "";
  try {
    const parsed = JSON.parse(rawText);
    const message = parsed?.choices?.[0]?.message?.content;
    content = typeof message === "string" ? message : "";
  } catch {
    throw new Error("模型返回的不是合法 JSON");
  }
  if (!content.trim()) throw new Error("模型没有返回内容");

  return parseReview(content);
}

/** 把模型输出规整成固定结构：模型偶尔会多带 markdown 或漏字段，这里兜住。 */
export function parseReview(content: string): PhotoReview {
  const cleaned = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();

  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  const candidate = start >= 0 && end > start ? cleaned.slice(start, end + 1) : cleaned;

  try {
    const data = JSON.parse(candidate);
    return normalizeReview(data, content);
  } catch {
    // 解析失败也不抛：把原文当描述返回，用户至少能看到模型说了什么
    return {
      verdict: "review",
      score: 0,
      subject: content.slice(0, 120),
      issues: [],
      advice: content.slice(0, 240),
    };
  }
}

function normalizeReview(data: any, fallbackText: string): PhotoReview {
  const verdictRaw = String(data?.verdict ?? "").toLowerCase();
  const verdict: ReviewVerdict =
    verdictRaw === "keep" || verdictRaw === "reject" ? (verdictRaw as ReviewVerdict) : "review";

  const scoreNumber = Number(data?.score);
  const score = Number.isFinite(scoreNumber) ? Math.max(0, Math.min(100, Math.round(scoreNumber))) : 0;

  const issues = Array.isArray(data?.issues)
    ? data.issues.map((item: unknown) => String(item ?? "").trim()).filter(Boolean).slice(0, 3)
    : [];

  return {
    verdict,
    score,
    subject: String(data?.subject ?? "").trim() || fallbackText.slice(0, 120),
    issues,
    advice: String(data?.advice ?? "").trim(),
  };
}

function extractErrorMessage(status: number, rawText: string): string {
  try {
    const parsed = JSON.parse(rawText);
    const message = parsed?.error?.message ?? parsed?.message;
    if (typeof message === "string" && message.trim()) return `HTTP ${status}：${message.slice(0, 300)}`;
  } catch {
    // 非 JSON 错误体，落到下面
  }
  return `HTTP ${status}：${rawText.slice(0, 200)}`;
}

// ---- API Key 读写（原生安全存储）----

export async function loadApiKey(): Promise<string | null> {
  const value = await secureStore.get(API_KEY_STORAGE);
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export async function saveApiKey(value: string): Promise<void> {
  const trimmed = value.trim();
  if (trimmed) {
    await secureStore.set(API_KEY_STORAGE, trimmed);
  } else {
    await secureStore.remove(API_KEY_STORAGE);
  }
}

export async function clearApiKey(): Promise<void> {
  await secureStore.remove(API_KEY_STORAGE);
}
