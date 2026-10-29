import type { AppSettings, ArchiveTask, AuditResult, AgentReply, DeliveryPackage, DesktopApi, Device, MediaFile, NamePreset, TaskState, UserSession, QuarantineItem } from './contracts'
import type { LocalFileActionPreview, LocalFileActionReceipt } from './local-file-actions'

const delay = (ms = 140) => new Promise(resolve => setTimeout(resolve, ms))
const fileData: MediaFile[] = [
  { id: 'f1', name: '海边日落_0421.ARW', path: '/Volumes/SD_CARD/DCIM/海边日落_0421.ARW', kind: 'photo', size: '48.2 MB', capturedAt: '2025-04-21 18:42', device: 'Sony A7 IV', location: '青岛 · 石老人', quality: '曝光正常', qualityState: 'good' },
  { id: 'f2', name: '海边日落_0422.ARW', path: '/Volumes/SD_CARD/DCIM/海边日落_0422.ARW', kind: 'photo', size: '49.1 MB', capturedAt: '2025-04-21 18:43', device: 'Sony A7 IV', location: '青岛 · 石老人', quality: '轻微过曝，待复核', qualityState: 'review' },
  { id: 'f3', name: 'DSC_1084.JPG', path: '/Volumes/SD_CARD/DCIM/DSC_1084.JPG', kind: 'photo', size: '8.6 MB', capturedAt: '2025-04-21 18:51', device: 'Sony A7 IV', location: '青岛 · 石老人', quality: '清晰', qualityState: 'good' },
  { id: 'f4', name: '海边片段_01.MP4', path: '/Volumes/SD_CARD/DCIM/海边片段_01.MP4', kind: 'video', size: '326 MB', capturedAt: '2025-04-21 19:03', device: 'Sony A7 IV', location: '青岛 · 石老人', quality: '视频 · 未分析', qualityState: 'unanalyzed' },
  { id: 'f5', name: '山野纪行_009.CR3', path: '/Volumes/归档/山野纪行/009.CR3', kind: 'photo', size: '36.4 MB', capturedAt: '2025-04-18 09:14', device: 'Canon R6', location: '浙江 · 安吉', quality: '疑似失焦，待复核', qualityState: 'review' },
  { id: 'f6', name: '拍摄备注.xmp', path: '/Volumes/SD_CARD/DCIM/拍摄备注.xmp', kind: 'other', size: '4 KB', capturedAt: '2025-04-21 18:42', device: '关联文件', location: '—', quality: '关联文件 · 保留', qualityState: 'unanalyzed' },
]
const taskData: ArchiveTask[] = [
  { id: 'YX-20250422-014', name: '春日人像 · 本地备份', state: 'running', createdAt: '今天 09:41', files: 1248, size: '42.8 GB', progress: 72, speed: '186 MB/s', phase: 'SHA-256 校验', source: 'SD_CARD/DCIM', destinations: [{ id: 'local', name: 'Mac Studio / 摄影归档', state: 'running', progress: 72 }] },
  { id: 'YX-20250421-009', name: '山野纪行 · 年度整理', state: 'success', createdAt: '昨天 16:28', files: 842, size: '28.4 GB', progress: 100, speed: '—', phase: '已完成', source: 'CFexpress/PRIVATE', destinations: [{ id: 'local', name: '本机硬盘', state: 'success', progress: 100 }, { id: 'baidu', name: '百度网盘', state: 'success', progress: 100 }] },
  { id: 'YX-20250419-003', name: '城市漫步 · 副本同步', state: 'partial', createdAt: '4 月 19 日', files: 324, size: '12.1 GB', progress: 100, speed: '—', phase: '部分完成', source: '移动硬盘/DCIM', destinations: [{ id: 'local', name: '本机硬盘', state: 'success', progress: 100 }] },
]
const devices: Device[] = [
  { id: 'mac-1', name: 'Mac Studio · 影序桌面', platform: 'macOS', online: true, lastSeen: '当前设备', current: true },
  { id: 'phone-1', name: 'iPhone 16 Pro · Pocket', platform: 'iOS', online: true, lastSeen: '刚刚' },
  { id: 'win-1', name: '摄影工作站', platform: 'Windows', online: false, lastSeen: '昨天 21:14' },
]
const defaultSettings: AppSettings = { theme: 'light', startOnLogin: true, backgroundOnClose: true, verify: true, autoAnalyze: true, similarity: 88, maxUploadMbps: 0, syncHistory: true, syncAgent: true, syncThumbnails: true, syncPaths: false, webhookEnabled: false, webhookUrl: '', aiProvider: '本地规则优先' }
let settings = { ...defaultSettings }
let tasks = taskData.map(task => structuredClone(task))
let lastSession: UserSession | null = null
let presets: NamePreset[] = [
  { id: 'preset-camera', name: '相机导入 · 默认规则', pattern: '日期 / 事件 / 设备', isDefault: true },
  { id: 'preset-client', name: '客户交付', pattern: '客户 / 项目 / 日期', isDefault: false },
]
let packages: DeliveryPackage[] = []

export function createMockApi(): DesktopApi {
  const demoQuarantine: QuarantineItem[] = []
  const pendingActions = new Map<string, LocalFileActionPreview>()
  let actionSequence = 0
  return {
    fileActions: {
      async selectDirectory() { await delay(80); return { path: '/演示目标目录', purpose: 'destination', grantId: 'demo-directory-grant' } },
      async prepare(request) {
        await delay(60)
        const actionId = `demo-action-${++actionSequence}`
        const media = 'mediaId' in request ? fileData.find((item) => item.id === request.mediaId) : undefined
        const quarantineItem = request.tool === 'cleanup_quarantine' ? demoQuarantine.find((item) => item.id === request.quarantineId) : undefined
        if ((request.tool !== 'cleanup_quarantine' && !media) || (request.tool === 'cleanup_quarantine' && !quarantineItem)) throw new Error('演示数据中找不到该素材')
        const name = request.tool === 'cleanup_quarantine' ? quarantineItem!.name : media!.name
        const sourcePath = request.tool === 'cleanup_quarantine' ? `演示隔离目录/${name}` : media!.path
        const targetName = request.tool === 'rename_item' ? request.newName : name
        const targetPath = request.tool === 'move_item' ? `/演示目标目录/${name}` : request.tool === 'rename_item' ? `${media!.path.slice(0, media!.path.lastIndexOf('/') + 1)}${targetName}` : '系统废纸篓中的演示隔离副本'
        const preview: LocalFileActionPreview = {
          actionId, tool: request.tool, ...(media ? { mediaId: media.id } : {}), objectName: name,
          ...(request.tool !== 'cleanup_quarantine' ? { sourcePath: media!.path } : {}),
          ...(request.tool === 'rename_item' ? { targetName } : {}), targetPath,
          sha256: '演示摘要 · 非真实文件哈希', effect: request.tool === 'cleanup_quarantine' ? '仅演示将隔离副本移入系统废纸篓；原片不变' : request.tool === 'rename_item' ? '仅演示在原目录重命名' : '仅演示移动到选定目录',
          requiresConfirmation: true, expiresAtMs: Date.now() + 5 * 60_000,
        }
        pendingActions.set(actionId, preview)
        return preview
      },
      async confirm(actionId, accepted) {
        await delay(80)
        const preview = pendingActions.get(actionId)
        if (!preview) throw new Error('演示操作不存在或已处理')
        pendingActions.delete(actionId)
        const status = accepted ? 'succeeded' : 'declined'
        const receipt: LocalFileActionReceipt = {
          actionId, tool: preview.tool, status, ...(preview.mediaId ? { mediaId: preview.mediaId } : {}),
          ...(preview.sourcePath ? { sourcePath: preview.sourcePath } : {}), targetPath: preview.targetPath,
          ...(preview.sha256 ? { sha256: preview.sha256 } : {}), sourcePreserved: true,
          destinationCreated: accepted, userConfirmed: accepted, cloudExecutionAuthorized: false,
        }
        if (accepted && preview.tool === 'cleanup_quarantine') {
          const item = demoQuarantine.find((entry) => entry.name === preview.objectName)
          if (item) { item.state = 'trashed'; item.restoreViaSystemTrash = true }
        }
        return receipt
      },
      async stageCandidate(mediaId) {
        await delay(80)
        const media = fileData.find((item) => item.id === mediaId)
        if (!media || media.qualityState !== 'review') throw new Error('只有待复核演示素材可以复制到隔离目录')
        const item: QuarantineItem = { id: `demo-quarantine-${++actionSequence}`, mediaId, name: media.name, bytes: 0, sha256: '演示摘要 · 非真实文件哈希', state: 'isolated', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), restoreViaSystemTrash: false, originalPreserved: true }
        demoQuarantine.unshift(item)
        return { id: item.id, name: item.name, state: item.state, originalPreserved: true }
      },
      async listQuarantine() { await delay(40); return demoQuarantine.map((item) => ({ ...item })) },
    },
    auth: {
      configuration() { return { ready: false, emailOtp: false, usernamePassword: false, reason: "浏览器预览不会连接 CloudBase 或发送真实验证码。" } },
      async sendEmailCode() { throw new Error("浏览器预览不会发送真实邮箱验证码。") },
      async registerEmail() { throw new Error("浏览器预览不会创建真实账号。") },
      async resetPassword() { throw new Error("浏览器预览不会重置真实账号密码。") },
      async restore() { await delay(80); return lastSession },
      async login(method, identifier) {
        await delay(260)
        if (!identifier.trim()) throw new Error('请填写登录信息。')
        lastSession = { userId: 'demo-user-01', displayName: identifier.includes('@') ? identifier.split('@')[0] : '影序用户', email: identifier.includes('@') ? identifier : 'demo@yingxu.app', deviceId: 'mac-1', expiresAt: new Date(Date.now() + 86400000).toISOString() }
        return lastSession
      },
      async logout() { lastSession = null; await delay(80) },
    },
    workbench: { async snapshot() { await delay(); return { tasks: tasks.slice(0, 3), fileCount: 2486, totalSize: '1.28 TB', destinations: 2, reviewCount: 36 } } },
    backup: {
      async scan() { await delay(340); return { sources: [{ id: 'sd-1', name: 'SD_CARD', path: '/Volumes/SD_CARD/DCIM', files: 1248, size: '42.8 GB', free: '84.6 GB' }] } },
      async start(payload) {
        await delay(220)
        const task: ArchiveTask = { id: `YX-DEMO-${Date.now().toString().slice(-6)}`, name: '新建备份任务', state: 'running', createdAt: '刚刚', files: 1248, size: '42.8 GB', progress: 0, speed: '0 MB/s', phase: '准备复制', source: payload.sourceId, destinations: payload.destinations.map(id => ({ id, name: id === 'local' ? '本机硬盘' : id === 'baidu' ? '百度网盘' : '本地目录', state: 'queued', progress: 0 })) }
        tasks = [task, ...tasks]; return task
      },
      async control(id, action) {
        await delay(130)
        const task = tasks.find(item => item.id === id) || tasks[0]
        task.state = ({ pause: 'paused', resume: 'running', cancel: 'cancelled', retry: 'running' } as Record<string, TaskState>)[action]
        task.phase = ({ pause: '已暂停', resume: '继续处理中', cancel: '已取消', retry: '正在重试' } as Record<string, string>)[action]
        return task
      },
    },
    transfer: {
      async inspect() { await delay(260); return fileData.map(file => ({ ...file, selected: file.kind !== 'other' })) },
      async start(ids, target, confirmSourceRemoval) {
        await delay(220)
        return { id: `YX-MOVE-${Date.now().toString().slice(-5)}`, name: '老内容转移演示', state: confirmSourceRemoval ? 'running' : 'failed', createdAt: '刚刚', files: ids.length, size: '12.4 GB', progress: 0, speed: '0 MB/s', phase: confirmSourceRemoval ? '复制并校验目标' : '等待来源移除确认', source: '旧素材目录', destinations: [{ id: 'local', name: target, state: 'queued', progress: 0 }] }
      },
    },
    library: {
      async searchPage(query, kind, quality, limit = 200, offset = 0) {
        await delay(80);
        const filtered = fileData.filter(file => (!query || `${file.name} ${file.device} ${file.location} ${file.path}`.toLowerCase().includes(query.toLowerCase())) && (kind === 'all' || file.kind === kind) && (quality === 'all' || file.qualityState === quality));
        const items = filtered.slice(offset, offset + limit);
        return { items, total: filtered.length, offset, limit, hasMore: offset + items.length < filtered.length };
      },
      async search(query, kind, quality) { await delay(100); return fileData.filter(file => (!query || `${file.name} ${file.device} ${file.location} ${file.path}`.toLowerCase().includes(query.toLowerCase())) && (kind === 'all' || file.kind === kind) && (quality === 'all' || file.qualityState === quality)) },
    },
    history: {
      async list(query, state) { await delay(90); return tasks.filter(task => (!query || `${task.name} ${task.id}`.toLowerCase().includes(query.toLowerCase())) && (state === 'all' || task.state === state)) },
      async detail(id) { await delay(80); return tasks.find(task => task.id === id) || tasks[0] },
      async rename(id, name) { await delay(80); const task = tasks.find(item => item.id === id); if (task) task.name = name },
      async clear() { await delay(100); tasks = [] },
      async retry(id, destinationId) { await delay(120); const task = tasks.find(item => item.id === id) || tasks[0]; const destination = task.destinations.find(item => item.id === destinationId); if (destination) { destination.state = 'running'; destination.progress = 0; destination.error = undefined } task.state = 'running'; return task },
    },
    agent: {
      async ask(text): Promise<AgentReply> {
        await delay(240)
        const matches = fileData.filter(file => `${file.name} ${file.location}`.toLowerCase().includes(text.toLowerCase().includes('海边') ? '海边' : ''))
        const proposedAction = /(改名|移动|上传|清理|整理)/.test(text) ? { kind: text.includes('上传') ? 'upload' as const : text.includes('移动') ? 'move' as const : text.includes('清理') ? 'cleanup' as const : 'rename' as const, label: text, target: text.includes('上传') ? '百度网盘 / 摄影归档' : '摄影归档 / 待确认', objects: matches.length ? matches.map(file => file.name) : [text.includes('清理') ? '待处理废片目录中的 2 个候选副本' : '所选的 3 个影像文件'] } : undefined
        return { text: proposedAction ? '我先列出操作对象和目标。确认后才会执行；本次为演示，不会修改文件。' : matches.length ? `演示检索到 ${matches.length} 个匹配素材。真实检索会返回文件 ID、匹配原因与本地元数据。` : '我可以按文件名、日期、设备、地点和归档记录检索。当前结果为演示数据。', matches: matches.map(media => ({ kind: "media" as const, media })), proposedAction }
      },
      async confirm(_id, accepted) { await delay(180); return accepted ? '演示操作已确认；真实文件操作尚未接入。' : '已取消演示操作，文件未变更。' },
    },
    account: {
      async devices() { await delay(100); return devices },
      async revoke(id) { await delay(100); const device = devices.find(item => item.id === id); if (device) device.online = false },
      async updateSync(next) { settings = { ...settings, ...next }; await delay(100) },
    },
    settings: {
      async get() { await delay(70); return { ...settings } },
      async update(next) { settings = { ...settings, ...next }; await delay(80); return { ...settings } },
      async reset() { settings = { ...defaultSettings }; await delay(100); return { ...settings } },
    },
    presets: {
      async list() { await delay(60); return presets },
      async save(preset) { await delay(80); presets = presets.some(item => item.id === preset.id) ? presets.map(item => item.id === preset.id ? preset : item) : [...presets, preset]; return preset },
      async remove(id) { await delay(60); presets = presets.filter(item => item.id !== id) },
    },
    audit: {
      async run(): Promise<AuditResult> { await delay(420); return { checked: 2486, healthy: 2484, issues: [{ path: '山野纪行/视频/clip_07.MP4', reason: '目标盘当前离线，无法抽查', recoverable: true }, { path: '城市漫步/IMG_2840.JPG', reason: '文件哈希与上次记录不符，需复核', recoverable: true }], lastRun: '刚刚（演示）' } },
      async restore(path, destination) { await delay(220); return `已生成恢复演示：${path} → ${destination}。真实恢复尚未接入。` },
    },
    delivery: {
      async create(name, ids, includeContactSheet, includeManifest) { await delay(250); const item = { id: `PKG-${Date.now().toString().slice(-5)}`, name, items: ids.length, includeContactSheet, includeManifest, createdAt: '刚刚' }; packages = [item, ...packages]; return item },
    },
  }
}
