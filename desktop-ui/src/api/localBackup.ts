// 真实本机备份链路（非演示）：目录授权 → 扫描 → 计划 → 任务 → 校验副本 → 网盘排队。
// 所有调用都走原生宿主；浏览器环境一律拒绝，避免把演示数据当作真实结果。
import { invoke, isTauri } from '@tauri-apps/api/core'
import { nativeCoreRequest } from './nativeAuth'

export type PickedDirectory = { path: string; purpose: 'source' | 'destination' }

export type BackupScan = { path: string; files: number; bytes: number }

export type BackupPlan = {
  id: string
  createdAt: string
  files: { relative: string }[]
  totalBytes: number
}

export type JobTarget = { id: string; path: string; state: string; copied: number; skipped: number; verified: number; failed: number }

export type BackupJob = {
  id: string
  name: string
  kind: string
  state: string
  phase: string
  source: string
  verify: boolean
  totalBytes: number
  files: number
  progress: number
  targets: JobTarget[]
  error?: string | null
}

export type VerifiedReplica = { id: string; path: string; bytes?: number; sha256: string }

export type BaiduUploadTask = {
  taskId: string
  localPath: string
  remotePath: string
  bytes: number
  state: string
  error?: string
  chunksDone: number
  chunksTotal: number
  verifiedRemote: boolean
  verification?: string
}

function requireNative(): void {
  if (!isTauri()) throw new Error('真实备份只在影序桌面端可用。')
}

export async function pickDirectory(purpose: 'source' | 'destination'): Promise<PickedDirectory | null> {
  requireNative()
  const picked = await invoke<PickedDirectory | null>('select_directory', { purpose })
  return picked ?? null
}

export async function scanSource(source: string): Promise<BackupScan> {
  requireNative()
  const files = await nativeCoreRequest<unknown[]>('backup.scan', { source })
  const list: unknown[] = Array.isArray(files) ? files : []
  // 扫描结果只用于展示规模，不据此判定可备份。
  const bytes = list.reduce<number>((sum, item) => {
    const size = (item as { fingerprint?: { size?: number } })?.fingerprint?.size
    return sum + (typeof size === 'number' ? size : 0)
  }, 0)
  return { path: source, files: list.length, bytes }
}

export async function previewBackup(input: { source: string; destinations: string[]; name: string; verify: boolean }): Promise<BackupPlan> {
  requireNative()
  if (!input.destinations.length) throw new Error('请先选择至少一个本地备份目录。')
  return nativeCoreRequest<BackupPlan>('backup.preview', {
    source: input.source,
    destinations: input.destinations,
    name: input.name,
    verify: input.verify,
  })
}

export async function startBackupJob(planId: string): Promise<BackupJob> {
  requireNative()
  return nativeCoreRequest<BackupJob>('jobs.start', {
    planId,
    confirmed: true,
    idempotencyKey: `ui-${planId}`,
  })
}

export async function jobDetail(jobId: string): Promise<BackupJob> {
  requireNative()
  return nativeCoreRequest<BackupJob>('jobs.detail', { jobId })
}

/** 本机宿主自带后台 worker；界面只负责轮询任务结果，不重复驱动队列。 */
export async function waitForJob(jobId: string, onJob?: (job: BackupJob) => void, timeoutMs = 30 * 60 * 1000): Promise<BackupJob> {
  requireNative()
  const terminal = new Set(['success', 'partial', 'failed', 'cancelled'])
  const started = Date.now()
  for (;;) {
    const job = await jobDetail(jobId)
    onJob?.(job)
    if (terminal.has(job.state)) return job
    if (Date.now() - started > timeoutMs) throw new Error('任务仍在进行，请稍后在归档记录中查看结果。')
    await new Promise(resolve => setTimeout(resolve, 800))
  }
}

export async function listVerifiedReplicas(): Promise<VerifiedReplica[]> {
  requireNative()
  return nativeCoreRequest<VerifiedReplica[]>('replicas.list')
}

export async function baiduStatus(): Promise<{ authorized: boolean; appFolder?: string }> {
  requireNative()
  return invoke<{ authorized: boolean; appFolder?: string }>('baidu_request', { action: 'status', replicaId: null, taskId: null, confirmed: false })
}

export async function prepareBaiduUpload(replicaId: string, taskId: string): Promise<BaiduUploadTask> {
  requireNative()
  return invoke<BaiduUploadTask>('baidu_request', { action: 'prepare', replicaId, taskId, confirmed: false })
}

export async function runBaiduUpload(taskId: string): Promise<BaiduUploadTask> {
  requireNative()
  return invoke<BaiduUploadTask>('baidu_request', { action: 'run', replicaId: null, taskId, confirmed: true })
}

/**
 * 备份成功且副本通过校验后，按用户勾选把副本排进百度上传队列。
 * 局部失败只记录在返回结果里，不影响本地备份结论。
 */
export async function queueVerifiedReplicasForBaidu(replicaIds: string[], onTask?: (task: BaiduUploadTask) => void): Promise<{ uploaded: BaiduUploadTask[]; failed: { replicaId: string; error: string }[] }> {
  requireNative()
  const uploaded: BaiduUploadTask[] = []
  const failed: { replicaId: string; error: string }[] = []
  for (const replicaId of replicaIds) {
    try {
      const prepared = await prepareBaiduUpload(replicaId, crypto.randomUUID())
      const result = await runBaiduUpload(prepared.taskId)
      uploaded.push(result)
      onTask?.(result)
    } catch (error) {
      failed.push({ replicaId, error: error instanceof Error ? error.message : String(error) })
    }
  }
  return { uploaded, failed }
}
