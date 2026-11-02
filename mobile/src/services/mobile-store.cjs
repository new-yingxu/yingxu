const KEYS = {
  history: "yingxuNativeHistoryV1",
  task: "yingxuNativeActiveTaskV1",
  folders: "yingxuNativeFoldersV1",
  preferences: "yingxuNativePreferencesV1",
  cloudOutbox: "yingxuNativeCloudOutboxV1",
};
const { publicMetadata } = require("./firebase-sync.cjs");

function createMobileStore(storage) {
  if (!storage || typeof storage.getItem !== "function") throw new TypeError("storage is required");
  async function read(key, fallback) {
    try {
      const raw = await storage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }
  return {
    async getHistory() {
      const records = await read(KEYS.history, []);
      return Array.isArray(records) ? records : [];
    },
    async saveHistory(record) {
      const records = await this.getHistory();
      const next = [record, ...records.filter((item) => item.id !== record.id)].slice(0, 500);
      await storage.setItem(KEYS.history, JSON.stringify(next));
      return next;
    },
    async deleteHistory(ids) {
      const drop = new Set((Array.isArray(ids) ? ids : [ids]).map((id) => String(id)));
      const records = await this.getHistory();
      const next = records.filter((item) => !drop.has(String(item.id)));
      await storage.setItem(KEYS.history, JSON.stringify(next));
      return next;
    },
    async clearHistory() {
      await storage.setItem(KEYS.history, JSON.stringify([]));
      return [];
    },
    async getActiveTask() {
      return read(KEYS.task, null);
    },
    async saveActiveTask(task) {
      if (task) await storage.setItem(KEYS.task, JSON.stringify(task));
      else await storage.removeItem(KEYS.task);
    },
    async getFolders() {
      return read(KEYS.folders, {});
    },
    async setFolder(kind, value) {
      const folders = await this.getFolders();
      folders[kind] = value;
      await storage.setItem(KEYS.folders, JSON.stringify(folders));
      return folders;
    },
    async getPreferences() {
      const value = await read(KEYS.preferences, {});
      return { sortByCaptureTime: true, includeDateFolder: true, includeVideos: true, preserveSourceStructure: false, ...value };
    },
    async savePreferences(value) {
      const next = { sortByCaptureTime: true, includeDateFolder: true, includeVideos: true, preserveSourceStructure: false, ...value };
      await storage.setItem(KEYS.preferences, JSON.stringify(next));
      return next;
    },
    async getCloudOutbox() {
      const value = await read(KEYS.cloudOutbox, { progress: null, history: [] });
      return { progress: value?.progress || null, history: Array.isArray(value?.history) ? value.history : [] };
    },
    async queueCloudProgress(record) {
      const outbox = await this.getCloudOutbox();
      outbox.progress = publicMetadata(record);
      await storage.setItem(KEYS.cloudOutbox, JSON.stringify(outbox));
      return outbox;
    },
    async clearCloudProgress() {
      const outbox = await this.getCloudOutbox();
      outbox.progress = null;
      await storage.setItem(KEYS.cloudOutbox, JSON.stringify(outbox));
      return outbox;
    },
    async queueCloudHistory(record) {
      const metadata = publicMetadata(record);
      if (!metadata.id) return this.getCloudOutbox();
      const outbox = await this.getCloudOutbox();
      outbox.history = [metadata, ...outbox.history.filter((item) => item.id !== metadata.id)].slice(0, 500);
      await storage.setItem(KEYS.cloudOutbox, JSON.stringify(outbox));
      return outbox;
    },
    async clearCloudHistory(id) {
      const outbox = await this.getCloudOutbox();
      outbox.history = outbox.history.filter((item) => item.id !== String(id));
      await storage.setItem(KEYS.cloudOutbox, JSON.stringify(outbox));
      return outbox;
    },
  };
}

module.exports = { createMobileStore, KEYS };
