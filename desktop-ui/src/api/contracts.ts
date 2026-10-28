export type Result<T> = { ok: true; data: T } | { ok: false; code: string; message: string }
export type TaskState = 'queued' | 'running' | 'paused' | 'success' | 'partial' | 'cancelled' | 'failed'
export type MediaKind = 'photo' | 'video' | 'other'

export interface UserSession { userId: string; displayName: string; email: string; deviceId: string; expiresAt: string }
export interface Device { id: string; name: string; platform: 'macOS' | 'Windows' | 'Android' | 'iOS' | '桌面端' | '移动端'; online: boolean; lastSeen: string; current?: boolean; revoked?: boolean }
export interface TaskDestination { id: string; name: string; state: TaskState; progress: number; error?: string }
export interface MediaFile { id: string; name: string; path: string; kind: MediaKind; size: string; capturedAt: string; device: string; location: string; quality: string; qualityState: 'good' | 'review' | 'unanalyzed'; selected?: boolean }
export interface ArchiveTask { id: string; name: string; state: TaskState; createdAt: string; files: number; size: string; progress: number; speed: string; phase: string; source: string; destinations: TaskDestination[]; error?: string }
export interface NamePreset { id: string; name: string; pattern: string; isDefault: boolean }
export interface AppSettings { theme: 'light' | 'dark'; startOnLogin: boolean; backgroundOnClose: boolean; verify: boolean; autoAnalyze: boolean; similarity: number; maxUploadMbps: number; syncHistory: boolean; syncAgent: boolean; syncThumbnails: boolean; syncPaths: boolean; webhookEnabled: boolean; webhookUrl: string; aiProvider: string }
export interface AgentReply { text: string; matches: MediaFile[]; proposedAction?: { kind: 'rename' | 'move' | 'upload' | 'cleanup'; label: string; target: string; objects: string[] } }
export interface AuditResult { checked: number; healthy: number; issues: { path: string; reason: string; recoverable: boolean }[]; lastRun: string }
export interface DeliveryPackage { id: string; name: string; items: number; includeContactSheet: boolean; includeManifest: boolean; createdAt: string }

/** Frontend contract only. The V2 preview uses MockDesktopApi; production adapters come later. */
export interface DesktopApi {
  auth: { restore(): Promise<UserSession | null>; login(method: 'wechat' | 'phone-code' | 'email-code' | 'email-password', identifier: string, proof: string): Promise<UserSession>; logout(): Promise<void> }
  workbench: { snapshot(): Promise<{ tasks: ArchiveTask[]; fileCount: number; totalSize: string; destinations: number; reviewCount: number }> }
  backup: { scan(): Promise<{ sources: { id: string; name: string; path: string; files: number; size: string; free: string }[] }>; start(payload: { sourceId: string; destinations: string[]; presetId: string; verify: boolean }): Promise<ArchiveTask>; control(taskId: string, action: 'pause' | 'resume' | 'cancel' | 'retry'): Promise<ArchiveTask> }
  transfer: { inspect(source: string, target: string): Promise<MediaFile[]>; start(files: string[], target: string, confirmSourceRemoval: boolean): Promise<ArchiveTask> }
  library: { search(query: string, kind: MediaKind | 'all', quality: string): Promise<MediaFile[]> }
  history: { list(query: string, state: TaskState | 'all'): Promise<ArchiveTask[]>; detail(taskId: string): Promise<ArchiveTask>; rename(taskId: string, name: string): Promise<void>; clear(): Promise<void>; retry(taskId: string, destinationId: string): Promise<ArchiveTask> }
  agent: { ask(text: string): Promise<AgentReply>; confirm(actionId: string, accepted: boolean): Promise<string> }
  account: { devices(): Promise<Device[]>; revoke(deviceId: string): Promise<void>; updateSync(settings: Pick<AppSettings, 'syncHistory' | 'syncAgent' | 'syncThumbnails' | 'syncPaths'>): Promise<void> }
  settings: { get(): Promise<AppSettings>; update(settings: Partial<AppSettings>): Promise<AppSettings>; reset(): Promise<AppSettings>; connectProvider(provider: 'baidu'): Promise<string> }
  presets: { list(): Promise<NamePreset[]>; save(preset: NamePreset): Promise<NamePreset>; remove(id: string): Promise<void> }
  audit: { run(): Promise<AuditResult>; restore(path: string, destination: string): Promise<string> }
  delivery: { create(name: string, fileIds: string[], includeContactSheet: boolean, includeManifest: boolean): Promise<DeliveryPackage> }
}
