const { organizeMedia } = require("./mobile-backup.cjs");

const PAGE_SIZE = 40;
const CHUNK_SIZE = 1024 * 1024;

class DesktopDownloadEngine {
  /** @param {{ client: any, token: string, mediaAccess: any, destinationRoot: string, eventName?: string, onUpdate?: (task: Record<string, any>) => void, now?: () => string }} options */
  constructor({ client, token, mediaAccess, destinationRoot, eventName = "", onUpdate = () => {}, now = () => new Date().toISOString() }) {
    if (!client || !token || !mediaAccess || !destinationRoot) throw new TypeError("desktop download dependencies are required");
    this.client = client;
    this.token = token;
    this.mediaAccess = mediaAccess;
    this.destinationRoot = destinationRoot;
    this.eventName = eventName;
    this.onUpdate = onUpdate;
    this.now = now;
    this.paused = false;
    this.queued = false;
    this.cancelled = false;
    this.pauseWaiters = [];
    this.transferId = "";
    this.current = null;
  }

  async listFiles() {
    const items = [];
    let offset = 0;
    let total = 0;
    do {
      const page = await this.client.request("desktop.files.list", { token: this.token, offset, limit: PAGE_SIZE });
      total = Number(page.total || 0);
      items.push(...(Array.isArray(page.items) ? page.items : []));
      if (page.next_offset == null) break;
      if (Number(page.next_offset) <= offset) throw new Error("电脑素材列表分页响应无效");
      offset = Number(page.next_offset);
    } while (items.length < total && items.length < 100000);
    if (items.length < total) throw new Error("电脑素材数量超过 App 单次读取上限；请换较小的素材来源重新扫描。");
    return items;
  }

  pause() {
    this.paused = true;
    this._publish({ status: "paused" });
  }

  resume() {
    this.paused = false;
    this.pauseWaiters.splice(0).forEach((resolve) => resolve());
    this._publish({ status: "copying" });
  }

  cancel() {
    this.cancelled = true;
    this.resume();
  }

  queue() {
    this.queued = true;
    this.resume();
  }

  async _waitIfPaused() {
    while (this.paused && !this.cancelled) await new Promise((resolve) => this.pauseWaiters.push(resolve));
  }

  _publish(patch = {}) {
    if (!this.current) return;
    this.current = { ...this.current, ...patch };
    this.onUpdate({ ...this.current });
  }

  async run(files) {
    if (this.current && ["scanning", "copying", "paused"].includes(this.current.status)) throw new Error("已有电脑素材导入任务正在运行");
    if (!Array.isArray(files) || files.length === 0) throw new Error("请先选择电脑素材");
    this.cancelled = false;
    this.queued = false;
    this.paused = false;
    this.current = {
      id: `desktop-import-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sourceName: "影序电脑素材", destinationName: "手机或外接存储", eventName: this.eventName,
      startedAt: this.now(), status: "copying", totalFiles: files.length,
      totalBytes: files.reduce((sum, file) => sum + Math.max(0, Number(file.size) || 0), 0),
      processedFiles: 0, copiedFiles: 0, skippedFiles: 0, verifiedFiles: 0, failedFiles: 0,
      bytesCopied: 0, currentFile: "", files: [],
    };
    let lastProgressAt = Date.now();
    let lastPublishedBytes = 0;
    this.onUpdate({ ...this.current });
    for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1) {
      const file = files[fileIndex];
      await this._waitIfPaused();
      if (this.cancelled || this.queued) break;
      let nativeTransfer = "";
      try {
        this._publish({ status: "copying", currentFile: file.name });
        const begin = await this.client.request("desktop.file.begin", { token: this.token, sourceId: file.source_id });
        if (!/^[a-f0-9]{64}$/i.test(String(begin.sha256 || "")) || Number(begin.size) !== Number(file.size)) {
          throw new Error("电脑来源文件大小或校验信息已变化，请重新扫描");
        }
        const organized = organizeMedia([{
          name: file.name, mediaType: file.media_type,
          modifiedAt: Number(file.modified_at || 0),
          capturedAt: file.captured_at ? Date.parse(file.captured_at) : null,
        }], this.eventName)[0];
        const write = await this.mediaAccess.beginIncomingWriteAsync(
          this.destinationRoot, organized.destinationRelativePath, begin.size, begin.sha256,
        );
        if (write.status === "skipped") {
          await this.client.request("desktop.file.finish", { token: this.token, sourceId: file.source_id });
          this.current.files.push({ name: file.name, size: file.size, status: "skipped", sha256: begin.sha256 });
          this.current.skippedFiles += 1;
          this.current.verifiedFiles += 1;
          this.current.processedFiles += 1;
          this.onUpdate({ ...this.current });
          continue;
        }
        if (write.status !== "receiving" || typeof write.transferId !== "string") throw new Error("手机目标存储没有开始接收文件");
        nativeTransfer = write.transferId;
        this.transferId = nativeTransfer;
        let offset = 0;
        while (offset < Number(begin.size)) {
          await this._waitIfPaused();
          if (this.cancelled || this.queued) break;
          const chunk = await this.client.request("desktop.file.chunk", {
            token: this.token, sourceId: file.source_id, offset, length: Math.min(CHUNK_SIZE, Number(begin.size) - offset),
          });
          const bytesRead = Number(chunk.bytes_read || 0);
          if (!bytesRead || bytesRead > CHUNK_SIZE || offset + bytesRead > Number(begin.size)) throw new Error("电脑传输分块长度无效");
          await this.mediaAccess.appendIncomingChunkAsync(nativeTransfer, chunk.data);
          offset += bytesRead;
          this.current.bytesCopied += bytesRead;
          if (this.current.bytesCopied - lastPublishedBytes >= 8 * 1024 * 1024 || Date.now() - lastProgressAt >= 2000) {
            lastPublishedBytes = this.current.bytesCopied;
            lastProgressAt = Date.now();
            this.onUpdate({ ...this.current });
          }
        }
        if (this.cancelled || this.queued) {
          await this.mediaAccess.abortIncomingWriteAsync(nativeTransfer).catch(() => {});
          nativeTransfer = "";
          this.transferId = "";
          break;
        }
        const sourceResult = await this.client.request("desktop.file.finish", { token: this.token, sourceId: file.source_id });
        if (sourceResult.sha256 !== begin.sha256 || Number(sourceResult.size) !== Number(begin.size)) throw new Error("电脑来源文件在传输期间发生变化");
        const saved = await this.mediaAccess.finishIncomingWriteAsync(nativeTransfer);
        nativeTransfer = "";
        this.transferId = "";
        this.current.files.push({ name: file.name, size: file.size, status: "copied", sha256: saved.sha256 });
        this.current.copiedFiles += 1;
        this.current.verifiedFiles += 1;
        this.current.processedFiles += 1;
        this.onUpdate({ ...this.current });
      } catch (cause) {
        if (nativeTransfer) await this.mediaAccess.abortIncomingWriteAsync(nativeTransfer).catch(() => {});
        this.transferId = "";
        const message = cause instanceof Error ? cause.message : "电脑素材导入失败";
        if (this.client.isClosed?.() || /连接已断开|连接已关闭|连接超时|响应超时/.test(message)) {
          const pendingFiles = files.map((item) => ({
            name: String(item.name || ""), size: Number(item.size || 0),
            modified_at: Number(item.modified_at || 0), captured_at: String(item.captured_at || ""),
            camera: String(item.camera || ""), media_type: String(item.media_type || "photo"),
          }));
          const record = { ...this.current, status: "queued", currentFile: file.name, pendingFiles,
            error: "电脑连接中断；已完成文件保留，重新连接后可重新扫描并继续，重复文件会校验后跳过。", finishedAt: this.now() };
          this.current = record;
          this.onUpdate({ ...record });
          return record;
        }
        this.current.failedFiles += 1;
        this.current.processedFiles += 1;
        this.current.files.push({ name: file.name, size: file.size, status: "failed", error: message });
        this.onUpdate({ ...this.current });
      }
    }
    const status = this.queued ? "queued" : this.cancelled ? "cancelled" : this.current.failedFiles ? "partial" : "completed";
    const pendingFiles = this.queued ? files.map((item) => ({
      name: String(item.name || ""), size: Number(item.size || 0),
      modified_at: Number(item.modified_at || 0), captured_at: String(item.captured_at || ""),
      camera: String(item.camera || ""), media_type: String(item.media_type || "photo"),
    })) : undefined;
    this.current = { ...this.current, status, currentFile: this.queued ? this.current.currentFile : "",
      ...(pendingFiles ? { pendingFiles } : {}), finishedAt: this.now() };
    this.onUpdate({ ...this.current });
    return this.current;
  }
}

module.exports = { DesktopDownloadEngine, PAGE_SIZE, CHUNK_SIZE };
