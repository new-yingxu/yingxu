const PHOTO_EXTENSIONS = new Set([
  ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff",
  ".bmp", ".gif", ".dng", ".arw", ".cr2", ".cr3", ".nef", ".nrw",
  ".orf", ".rw2", ".raf", ".pef", ".srw", ".3fr", ".ari", ".bay",
  ".cap", ".data", ".dcs", ".dcr", ".drf", ".eip", ".erf", ".fff",
  ".gpr", ".iiq", ".k25", ".kdc", ".mdc", ".mef", ".mos", ".mrw",
  ".obm", ".ptx", ".pxn", ".r3d", ".rwl", ".rwz", ".x3f",
]);
const VIDEO_EXTENSIONS = new Set([
  ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp",
  ".mpg", ".mpeg", ".wmv", ".webm", ".insv", ".lrv", ".braw", ".crm",
]);

function mediaTypeForName(name) {
  const match = String(name || "").toLowerCase().match(/\.[^.]+$/);
  const extension = match ? match[0] : "";
  if (VIDEO_EXTENSIONS.has(extension)) return "video";
  if (PHOTO_EXTENSIONS.has(extension)) return "photo";
  return null;
}

function safeSegment(value) {
  return String(value || "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/\s+/g, " ")
    .replace(/^[ .]+|[ .]+$/g, "")
    .slice(0, 120) || "未命名";
}

function dateParts(timestamp) {
  const date = new Date(Number(timestamp) || 0);
  if (Number.isNaN(date.getTime())) return { key: "unknown", label: "日期待确认" };
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return { key: `${year}-${month}-${day}`, label: `${year}年${month}月${day}日` };
}

function filterAndSortMedia(files, preferences = {}) {
  return files
    .filter((file) => mediaTypeForName(file.name))
    .filter((file) => preferences.includeVideos !== false || mediaTypeForName(file.name) !== "video")
    .sort((left, right) => {
      const leftTime = preferences.sortByCaptureTime === false ? left.modifiedAt : (left.capturedAt || left.modifiedAt);
      const rightTime = preferences.sortByCaptureTime === false ? right.modifiedAt : (right.capturedAt || right.modifiedAt);
      return (leftTime || 0) - (rightTime || 0);
    });
}

function organizeMedia(files, eventName = "", preferences = {}) {
  const cleanEvent = safeSegment(eventName.trim());
  return files.map((file) => {
    const timestamp = preferences.sortByCaptureTime === false ? file.modifiedAt : (file.capturedAt || file.modifiedAt);
    const date = dateParts(timestamp);
    if (preferences.preserveSourceStructure === true) {
      const originalFolders = String(file.relativePath || file.name).replace(/\\/g, "/")
        .split("/").slice(0, -1).filter((part) => part && part !== "." && part !== "..")
        .map(safeSegment);
      return {
        ...file,
        destinationRelativePath: [...(eventName.trim() ? [cleanEvent] : []), ...originalFolders, safeSegment(file.name)].join("/"),
      };
    }
    const eventParts = [preferences.includeDateFolder === false ? "" : date.label, eventName.trim() ? cleanEvent : "", "手机拍摄", "照片及视频原片"]
      .filter(Boolean);
    const mediaFolder = file.mediaType === "video" ? "视频" : "照片";
    return {
      ...file,
      destinationRelativePath: [...eventParts, mediaFolder, safeSegment(file.name)].join("/"),
    };
  });
}

/**
 * @param {{ mediaAccess: any, onUpdate?: (task: Record<string, any>) => void, now?: () => string }} options
 */
class MobileBackupEngine {
  /** @param {{ mediaAccess: any, onUpdate?: (task: Record<string, any>) => void, now?: () => string }} options */
  constructor({ mediaAccess, onUpdate = () => {}, now = () => new Date().toISOString() }) {
    if (!mediaAccess) throw new TypeError("mediaAccess is required");
    this.mediaAccess = mediaAccess;
    this.onUpdate = onUpdate;
    this.now = now;
    this.cancelled = false;
    this.queued = false;
    this.paused = false;
    this.offlineTargets = new Set();
    this.pauseWaiters = [];
    this.current = null;
  }

  async scan(sourceUri, preferences = {}) {
    const candidates = await this.mediaAccess.scanMediaAsync(sourceUri);
    return filterAndSortMedia(candidates.map((file) => ({ ...file, mediaType: mediaTypeForName(file.name) })), preferences);
  }

  pause() {
    this.paused = true;
    this._publish({ status: "paused" });
  }

  resume() {
    this.paused = false;
    for (const resolve of this.pauseWaiters.splice(0)) resolve();
    this._publish({ status: "copying" });
  }

  cancel() {
    this.cancelled = true;
    this.paused = false;
    for (const resolve of this.pauseWaiters.splice(0)) resolve();
  }

  async _waitIfPaused() {
    while (this.paused && !this.cancelled) {
      await new Promise((resolve) => this.pauseWaiters.push(resolve));
    }
  }

  _publish(patch = {}) {
    if (!this.current) return;
    this.current = { ...this.current, ...patch };
    this.onUpdate({ ...this.current });
  }

  /** @param {{ sourceUri: string, destinationUri: string, destinationTargets?: Array<{ uri: string, name: string }> | null, eventName?: string, destinationName?: string, taskId?: string, sourceKind?: "camera" | "archive", notes?: { location?: string, device?: string, notes?: string }, preferences?: { sortByCaptureTime?: boolean, includeDateFolder?: boolean, includeVideos?: boolean, preserveSourceStructure?: boolean } }} options */
  async run({ sourceUri, destinationUri, destinationTargets = null, eventName = "", destinationName = "手机存储", taskId = "", sourceKind = "camera", notes = {}, preferences = {} }) {
    if (this.current && ["scanning", "copying", "paused"].includes(this.current.status)) {
      throw new Error("已有手机备份任务正在运行");
    }
    this.cancelled = false;
    this.queued = false;
    this.paused = false;
    this.offlineTargets.clear();
    const startedAt = this.now();
    this.current = {
      id: taskId || `mobile-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sourceName: sourceUri.split(/[\\/]/).filter(Boolean).pop() || "手机存储卡",
      sourceKind,
      destinationName,
      eventName: eventName.trim(),
      location: String(notes.location || "").slice(0, 160),
      cameraDevice: String(notes.device || "").slice(0, 160),
      notes: String(notes.notes || "").slice(0, 2000),
      startedAt,
      status: "scanning",
      totalFiles: 0,
      totalBytes: 0,
      processedFiles: 0,
      copiedFiles: 0,
      skippedFiles: 0,
      verifiedFiles: 0,
      failedFiles: 0,
      currentFile: "",
      files: [],
    };
    this.onUpdate({ ...this.current });

    let files;
    try {
      const targets = Array.isArray(destinationTargets) && destinationTargets.length
        ? destinationTargets
        : [{ uri: destinationUri, name: destinationName }];
      files = organizeMedia(await this.scan(sourceUri, preferences), eventName, preferences);
      this._publish({ status: "copying", totalFiles: files.length,
        totalBytes: files.reduce((sum, file) => sum + Math.max(0, Number(file.size) || 0) * targets.length, 0),
        destinationName: targets.map((target) => target.name).join("、") });
      if (files.length === 0) throw new Error("没有找到支持的照片、RAW 或视频文件");

      for (const file of files) {
        await this._waitIfPaused();
        if (this.cancelled) break;
        this._publish({ status: "copying", currentFile: file.name });
        const results = [];
        for (const target of targets) {
          if (this.offlineTargets.has(target.uri)) {
            results.push({ targetName: target.name, status: "queued" });
            this.queued = true;
            continue;
          }
          try {
            const result = await this.mediaAccess.copyFileVerifiedAsync(
              file.uri, target.uri, file.destinationRelativePath, file,
            );
            results.push({ targetName: target.name, status: result.status, sha256: result.sha256 });
          } catch (error) {
            if (error?.code === "DESKTOP_OFFLINE") {
              this.offlineTargets.add(target.uri);
              this.queued = true;
              results.push({ targetName: target.name, status: "queued" });
              continue;
            }
            results.push({ targetName: target.name, status: "failed", error: error.message || "复制失败" });
          }
        }
        const failures = results.filter((result) => result.status === "failed");
        const copied = results.filter((result) => result.status === "copied");
        const queuedTargets = results.filter((result) => result.status === "queued");
        if (failures.length) this.current.failedFiles += 1;
        if (copied.length) this.current.copiedFiles += 1;
        else if (!failures.length && !queuedTargets.length) this.current.skippedFiles += 1;
        if (!failures.length && !queuedTargets.length) {
          this.current.verifiedFiles += 1;
        }
        this.current.files.push({ name: file.name, size: file.size, mediaType: file.mediaType,
          status: failures.length ? "partial" : queuedTargets.length ? "queued" : copied.length ? "copied" : "skipped",
          targets: results, sha256: results.find((result) => result.sha256)?.sha256 || "" });
        this.current.processedFiles += 1;
        this.onUpdate({ ...this.current });
      }

      const status = this.queued ? "queued" : this.cancelled ? "cancelled" : this.current.failedFiles ? "partial" : "completed";
      const record = { ...this.current, status, finishedAt: this.now(), currentFile: "" };
      this.current = record;
      this.onUpdate({ ...record });
      return record;
    } catch (error) {
      const record = { ...this.current, status: "failed", error: error.message || "备份失败", finishedAt: this.now() };
      this.current = record;
      this.onUpdate({ ...record });
      return record;
    }
  }
}

module.exports = {
  PHOTO_EXTENSIONS,
  VIDEO_EXTENSIONS,
  MobileBackupEngine,
  mediaTypeForName,
  filterAndSortMedia,
  organizeMedia,
  safeSegment,
};
