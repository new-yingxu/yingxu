import { getVersion } from "@tauri-apps/api/app";
import { invoke, isTauri } from "@tauri-apps/api/core";
import appPackage from "../../package.json";

const RELEASES_API = "https://api.github.com/repos/new-yingxu/yingxu/releases?per_page=30";
const RELEASES_PAGE = "https://github.com/new-yingxu/yingxu/releases";

export type UpdateCheckResult =
  | { kind: "available"; currentVersion: string; latestVersion: string; releaseUrl: string; downloadUrl: string | null; notes: string }
  | { kind: "up-to-date"; currentVersion: string; latestVersion: string }
  | { kind: "no-release"; currentVersion: string };

interface ParsedVersion {
  major: number;
  minor: number;
  patch: number;
  prerelease: string[];
}

interface GithubRelease {
  tag_name?: unknown;
  html_url?: unknown;
  draft?: unknown;
  body?: unknown;
  assets?: unknown;
}

function parseVersion(value: string): ParsedVersion | null {
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/);
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ? match[4].split(".") : [],
  };
}

function compareVersions(left: string, right: string): number {
  const a = parseVersion(left);
  const b = parseVersion(right);
  if (!a || !b) return 0;
  for (const key of ["major", "minor", "patch"] as const) {
    if (a[key] !== b[key]) return a[key] > b[key] ? 1 : -1;
  }
  if (!a.prerelease.length || !b.prerelease.length) {
    if (a.prerelease.length === b.prerelease.length) return 0;
    return a.prerelease.length ? -1 : 1;
  }
  const length = Math.max(a.prerelease.length, b.prerelease.length);
  for (let index = 0; index < length; index += 1) {
    const partA = a.prerelease[index];
    const partB = b.prerelease[index];
    if (partA === undefined || partB === undefined) return partA === undefined ? -1 : 1;
    if (partA === partB) continue;
    const numericA = /^\d+$/.test(partA);
    const numericB = /^\d+$/.test(partB);
    if (numericA && numericB) return Number(partA) > Number(partB) ? 1 : -1;
    if (numericA !== numericB) return numericA ? -1 : 1;
    return partA > partB ? 1 : -1;
  }
  return 0;
}

function isProjectReleaseUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    const releasesPath = "/new-yingxu/yingxu/releases";
    return url.protocol === "https:" && url.hostname === "github.com" &&
      url.username === "" && url.password === "" && url.port === "" &&
      (url.pathname === releasesPath || url.pathname.startsWith(`${releasesPath}/`));
  } catch {
    return false;
  }
}

function releaseDownloadUrl(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const isMac = /Macintosh|Mac OS X/i.test(navigator.userAgent);
  const extensions = isMac ? [".dmg", ".app.tar.gz"] : [".exe", ".msi"];
  const candidate = value.find((asset) => {
    if (!asset || typeof asset !== "object") return false;
    const row = asset as Record<string, unknown>;
    const name = row.name;
    return typeof name === "string" && extensions.some((extension) => name.toLowerCase().endsWith(extension));
  }) as Record<string, unknown> | undefined;
  return isProjectReleaseUrl(candidate?.browser_download_url) ? candidate.browser_download_url : null;
}

export async function currentAppVersion(): Promise<string> {
  if (isTauri()) {
    try {
      return await getVersion();
    } catch {
      // Keep the packaged manifest version available if the native API is unavailable.
    }
  }
  return appPackage.version;
}

export async function checkForUpdates(fetcher: typeof fetch = fetch): Promise<UpdateCheckResult> {
  const currentVersion = await currentAppVersion();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetcher(RELEASES_API, {
      headers: { Accept: "application/vnd.github+json" },
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 404) return { kind: "no-release", currentVersion };
      throw new Error(response.status === 403
        ? "GitHub 暂时限制了版本查询，请稍后重试。"
        : `版本查询失败（HTTP ${response.status}）。`);
    }
    const releases: unknown = await response.json();
    if (!Array.isArray(releases)) throw new Error("版本服务返回的数据格式无效。");
    const candidates = (releases as GithubRelease[])
      .filter((release) => release.draft !== true &&
        typeof release.tag_name === "string" && parseVersion(release.tag_name) &&
        releaseDownloadUrl(release.assets) !== null)
      .sort((left, right) => compareVersions(String(right.tag_name), String(left.tag_name)));
    const newest = candidates[0];
    if (!newest || typeof newest.tag_name !== "string" || !isProjectReleaseUrl(newest.html_url)) {
      return { kind: "no-release", currentVersion };
    }
    const latestVersion = newest.tag_name.replace(/^v/i, "");
    if (compareVersions(latestVersion, currentVersion) <= 0) {
      return { kind: "up-to-date", currentVersion, latestVersion };
    }
    return {
      kind: "available",
      currentVersion,
      latestVersion,
      releaseUrl: newest.html_url,
      downloadUrl: releaseDownloadUrl(newest.assets),
      notes: typeof newest.body === "string" ? newest.body.trim().slice(0, 5000) : "",
    };
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function openReleaseUrl(url: string): Promise<void> {
  if (!isProjectReleaseUrl(url)) throw new Error("下载地址不属于影序官方 GitHub Releases。");
  if (isTauri()) {
    await invoke("open_release_page", { url });
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export const releasesPageUrl = RELEASES_PAGE;
