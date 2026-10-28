// Isolated UI fixture. This is never an entry point of the distributed app.
import { createApp } from 'vue'
import App from '../src/App.vue'
import { createMockApi } from '../src/api/mock'
import type { AppApi, MediaFile, ArchiveTask, Confirmation } from '../src/api/contracts'
import '../src/style.css'

const base = createMockApi()
const longName = '客户项目_秋季城市影像归档_中文和English混合文件名_'.repeat(5)
const files: MediaFile[] = [{ id: 'qa-photo', name: `${longName}.jpg`, path: `D:\\客户素材\\${longName}\\照片.jpg`, kind: 'photo', size: '24 MB', capturedAt: '2026-10-29', device: 'QA Camera', location: '—', quality: '待复核', qualityState: 'review' }]
const task: ArchiveTask = { id: 'qa-task', name: longName, state: 'partial', createdAt: '2026-10-29', files: 2, size: '24 MB', progress: 100, speed: '—', phase: '部分完成', source: files[0]!.path, destinations: [{ id: 'qa-destination', name: `D:\\归档\\${longName}`, state: 'failed', progress: 40, error: '目标磁盘空间不足，请更换目标后重试。' }] }
const api: AppApi = {
  ...base, mode: 'native',
  auth: { ...base.auth, restore: async () => null },
  workbench: { snapshot: async () => ({ tasks: [task], fileCount: 1, totalSize: '24 MB', destinations: 1, reviewCount: 1 }) },
  history: { ...base.history, list: async () => [task], detail: async () => task },
  library: { ...base.library, searchPage: async () => ({ items: files, total: 1, offset: 0, limit: 100, hasMore: false }) },
  transfer: { ...base.transfer, inspect: async () => files },
  local: {
    health: async () => ({ workerReady: true }),
    capabilities: async () => ({ rawPreview: false, rawFullDecode: false, rawFormats: [], videoMetadata: false, videoFrameExtraction: false, videoContainers: [], netdiskRealUpload: false, netdiskProviders: [] }),
    subscribe: async () => () => {}, chooseDirectory: async () => 'D:\\QA目标',
    replicas: async () => [], indexSource: async () => ({ files: 1, changed: 1 }), lastIndexReport: () => null,
    analyze: async () => ({ metadata: { decodeReason: longName } }), thumbnail: async () => '',
    report: async () => ({ id: task.id, state: 'partial', source: task.source }), exportReport: async () => 'D:\\QA报告.json',
    startBackup: async () => task, confirmSourceRemoval: async () => {},
  },
}
createApp(App, { createApi: (confirm: (value: Confirmation) => Promise<boolean>): AppApi => ({
  ...api,
  history: { ...api.history, rename: async (_id, name) => {
    if (!await confirm({ title: '修改归档记录名称', description: '仅修改记录，不修改素材。', rows: [{ label: '新名称', value: name }] })) throw new Error('已取消修改')
    task.name = name
  } },
}) }).mount('#app')
