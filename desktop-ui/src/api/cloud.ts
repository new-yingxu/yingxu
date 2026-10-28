import type { AgentMatch, AgentReply, ModelService, ModelStatus } from './contracts'

/** Prepared for the cloud worktree draft API; deliberately not enabled by the app factory.
 * getUserToken must provide a short-lived user access token from the future native identity bridge.
 * Never pass CloudBase server API keys or persist tokens in frontend storage.
 */
export interface SyncPreferences {
  syncRecords: boolean
  syncAgentSessions: boolean
  syncThumbnails: boolean
  syncPaths: boolean
}
export class CloudError extends Error {
  modelDispatched?: boolean
  diagnosticCode?: string
  constructor(public code: string, message: string, public status = 0, public automaticRetryAllowed?: boolean) { super(message) }
}
type RecordValue = Record<string, unknown>
export type CloudToolName = 'search_backup_jobs' | 'get_backup_job'
export interface CloudToolDescriptor {
  name: string
  permission: 'read' | 'proposal_only'
  executableInCloud: boolean
  scope?: string
  requiresLocalConfirmation?: boolean
}
export interface CloudToolDirectory {
  orchestrator: 'langgraph'
  tools: CloudToolDescriptor[]
  executionAuthorized: false
}
export interface CloudSearchWindow { eventLimit: number; complete: boolean }
export interface CloudJobSearchResult {
  answer: string
  matches: Extract<AgentMatch, { kind: 'job' }>[]
  dataSource: string
  searchWindow: CloudSearchWindow
  executionAuthorized: false
}
export interface CloudJobLookupResult {
  jobs: Extract<AgentMatch, { kind: 'job' }>[]
  found: boolean
  searchWindow: CloudSearchWindow
  reason: 'NOT_IN_SEARCH_WINDOW' | null
  executionAuthorized: false
}
function object(value: unknown): RecordValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new CloudError('INVALID_CLOUD_RESPONSE', '云端响应结构无效')
  return value as RecordValue
}
function str(value: unknown): string {
  if (typeof value !== 'string') throw new CloudError('INVALID_CLOUD_RESPONSE', '云端字段类型无效')
  return value
}
function cursor(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new CloudError('INVALID_CURSOR', '同步游标无效')
  return value as number
}
function preferences(value: unknown): SyncPreferences {
  const row = object(value)
  const keys = ['syncRecords', 'syncAgentSessions', 'syncThumbnails', 'syncPaths'] as const
  for (const key of keys) if (typeof row[key] !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '同步偏好字段无效')
  return Object.fromEntries(keys.map(key => [key, row[key]])) as unknown as SyncPreferences
}
export function parseCloudReply(value: unknown): AgentReply {
  const row = object(value)
  if (!Array.isArray(row.matches)) throw new CloudError('INVALID_CLOUD_RESPONSE', 'Agent 匹配列表无效')
  if (row.orchestrator !== undefined && row.orchestrator !== 'langgraph') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '云端 Agent 编排器版本不受支持')
  if (row.duplicate !== undefined && typeof row.duplicate !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '重复请求标记无效')
  return {
    text: str(row.answer),
    ...(row.duplicate === undefined ? {} : { duplicate: row.duplicate }),
    ...(row.orchestrator === 'langgraph' ? { orchestrator: 'langgraph' as const } : {}),
    ...(typeof row.model === 'string' ? { model: row.model } : {}),
    ...(typeof row.provider === 'string' ? { provider: row.provider } : {}),
    ...(typeof row.persisted === 'boolean' ? { historySync: row.persisted ? 'synced' as const : 'not_synced' as const } : {}),
    matches: row.matches.map(value => {
      const match = object(value)
      // Do not accept the older deployed shape or invent local media paths/quality.
      if (match.kind !== 'job' || match.source !== 'synced_job_snapshot') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '云端任务契约尚未升级')
      return { kind: 'job', deviceId: str(match.deviceId), jobId: str(match.jobId), name: str(match.name), state: str(match.state), sequence: cursor(match.sequence), occurredAt: str(match.occurredAt), source: 'synced_job_snapshot' }
    }),
  }
}
function parseToolDirectory(value: unknown): CloudToolDirectory {
  const row = object(value)
  if (row.orchestrator !== 'langgraph' || row.executionAuthorized !== false || !Array.isArray(row.tools)) {
    throw new CloudError('CLOUD_CONTRACT_MISMATCH', 'LangGraph 工具目录无效，云端执行已关闭')
  }
  const tools = row.tools.map(value => {
    const item = object(value)
    if (item.permission !== 'read' && item.permission !== 'proposal_only') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '工具权限类型无效')
    if (typeof item.executableInCloud !== 'boolean' || (item.scope !== undefined && typeof item.scope !== 'string') || (item.requiresLocalConfirmation !== undefined && typeof item.requiresLocalConfirmation !== 'boolean')) {
      throw new CloudError('CLOUD_CONTRACT_MISMATCH', '工具目录字段无效')
    }
    if (item.permission === 'proposal_only' && item.executableInCloud) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '写操作不能标记为云端可执行')
    if (item.permission === 'proposal_only' && item.requiresLocalConfirmation !== true) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '写操作必须要求本地确认')
    return {
      name: str(item.name), permission: item.permission,
      executableInCloud: item.executableInCloud,
      ...(item.scope === undefined ? {} : { scope: str(item.scope) }),
      ...(item.requiresLocalConfirmation === undefined ? {} : { requiresLocalConfirmation: item.requiresLocalConfirmation }),
    } as CloudToolDescriptor
  })
  return { orchestrator: 'langgraph', tools, executionAuthorized: false }
}
function parseSearchWindow(value: unknown): CloudSearchWindow {
  const row = object(value)
  if (!Number.isSafeInteger(row.eventLimit) || (row.eventLimit as number) < 0 || typeof row.complete !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '任务查询窗口标记无效')
  return { eventLimit: row.eventLimit as number, complete: row.complete }
}
function parseReadToolEnvelope(value: unknown, expectedTool: CloudToolName): RecordValue {
  const row = object(value)
  if (row.tool !== expectedTool || row.executionAuthorized !== false) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '只读工具响应无效或包含未授权执行状态')
  return object(row.result)
}
function jobMatches(value: unknown): Extract<AgentMatch, { kind: 'job' }>[] {
  const parsed = parseCloudReply({ answer: '', matches: value })
  if (!parsed.matches.every((match): match is Extract<AgentMatch, { kind: 'job' }> => match.kind === 'job')) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '任务摘要类型无效')
  return parsed.matches
}
export function createCloudClient(options: {
  baseUrl: string
  getUserToken: () => Promise<string | null>
  fetch?: typeof fetch
  signal?: AbortSignal
  timeoutMs?: number
}) {
  const base = new URL(options.baseUrl)
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)
  if (base.username || base.password || base.search || base.hash || (base.protocol !== 'https:' && !(local && base.protocol === 'http:'))) throw new CloudError('INVALID_CLOUD_URL', '云端地址必须为 HTTPS；仅本地验证允许 HTTP')
  const baseUrl = base.href.replace(/\/$/, '')
  const transport = options.fetch ?? fetch
  async function request(path: string, method = 'GET', body?: unknown, onRequestStart?: () => void): Promise<unknown> {
    if (options.signal?.aborted) throw new CloudError('OWNER_SESSION_STOPPED', '账号会话已停止')
    const token = await options.getUserToken()
    if (options.signal?.aborted) throw new CloudError('OWNER_SESSION_STOPPED', '账号会话已停止')
    if (!token) throw new CloudError('AUTH_REQUIRED', '请先完成真实身份登录')
    const controller = new AbortController()
    const abort = () => controller.abort()
    options.signal?.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 15000)
    try {
      onRequestStart?.()
      const response = await transport(baseUrl + path, {
        method, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store',
        headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      let result: unknown
      try { result = await response.json() } catch { throw new CloudError('INVALID_CLOUD_RESPONSE', '云端未返回有效 JSON', response.status) }
      if (!response.ok) {
        const row = object(result)
        const detail = typeof row.detail === 'object' && row.detail !== null ? object(row.detail) : row
        const failure = new CloudError(typeof detail.code === 'string' ? detail.code : `HTTP_${response.status}`, '云端请求失败，请检查登录或服务状态', response.status, detail.automaticRetryAllowed === false ? false : undefined)
        if (response.status === 503 && failure.code === 'MODEL_NOT_CONFIGURED') failure.modelDispatched = false
        throw failure
      }
      if (options.signal?.aborted) throw new CloudError('OWNER_SESSION_STOPPED', '账号会话已停止')
      return result
    } catch (error) {
      if (options.signal?.aborted) throw new CloudError('OWNER_SESSION_STOPPED', '账号会话已停止')
      if (error instanceof CloudError) throw error
      // Never echo fetch errors, URLs, headers or tokens into UI/logs.
      throw new CloudError(controller.signal.aborted ? 'CLOUD_TIMEOUT' : 'CLOUD_UNREACHABLE', '云端请求未完成；请先核对本轮状态，勿在结果不明时重复提交')
    } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort) }
  }
  const id = (value: string) => {
    if (!value || value.length > 160) throw new CloudError('INVALID_ID', '会话或轮次编号无效')
    return encodeURIComponent(value)
  }
  async function submitModelTurn(query: string, identity: { sessionId: string; turnId: string }, consent: { explicitConsent: true }, resume: boolean) {
    if (consent?.explicitConsent !== true) throw new CloudError('MODEL_CONSENT_REQUIRED', '请逐次确认本次发送内容', 0, false)
    if (!query.trim() || query.length > 500) throw new CloudError('INVALID_QUERY', '请输入 1 至 500 字的查询', 0, false)
    if (!identity) throw new CloudError('MODEL_REQUIRES_SESSION_AND_TURN', '模型请求需要固定会话与轮次编号', 0, false)
    id(identity.sessionId); id(identity.turnId)
    let requestStarted = false
    try {
      const row = object(await request('/v1/agent/turns', 'POST', {
        query, sessionId: identity.sessionId, turnId: identity.turnId, useModel: true,
        ...(resume ? { resume: true } : {}),
      }, () => { requestStarted = true }))
      if (row.sessionId !== identity.sessionId || row.turnId !== identity.turnId || typeof row.persisted !== 'boolean') throw new CloudError('TURN_NOT_CONFIRMED', '模型结果未确认，可能已计费或未保存', 0, false)
      if (row.executionAuthorized !== false || typeof row.model !== 'string' || typeof row.provider !== 'string') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '模型回答状态无效，不能据此执行操作', 0, false)
      return parseCloudReply(row)
    } catch (error) {
      if (error instanceof CloudError) {
        if (!requestStarted) error.modelDispatched = false
        error.automaticRetryAllowed = false
        throw error
      }
      throw new CloudError('MODEL_OUTCOME_UNCERTAIN', '本轮结果未确认，可能已计费或未保存', 0, false)
    }
  }
  return {
    async toolDirectory(): Promise<CloudToolDirectory> {
      return parseToolDirectory(await request('/v1/agent/tools'))
    },
    async searchBackupJobs(query: string): Promise<CloudJobSearchResult> {
      if (typeof query !== 'string' || !query.trim() || query.length > 500) throw new CloudError('INVALID_QUERY', '请输入 1 至 500 字的任务查询')
      const directory = parseToolDirectory(await request('/v1/agent/tools'))
      const descriptor = directory.tools.find(tool => tool.name === 'search_backup_jobs')
      if (!descriptor || descriptor.permission !== 'read' || descriptor.executableInCloud !== true) throw new CloudError('TOOL_NOT_ALLOWED', '该查询工具未获只读云端调用许可', 0, false)
      const result = parseReadToolEnvelope(await request('/v1/agent/tools/search_backup_jobs', 'POST', { arguments: { query } }), 'search_backup_jobs')
      const reply = parseCloudReply(result)
      const window = parseSearchWindow(result.searchWindow)
      return { answer: reply.text, matches: jobMatches(result.matches), dataSource: str(result.dataSource), searchWindow: window, executionAuthorized: false }
    },
    async getBackupJob(jobId: string, deviceId?: string): Promise<CloudJobLookupResult> {
      if (typeof jobId !== 'string' || !jobId.trim() || jobId.length > 500 || (deviceId !== undefined && (typeof deviceId !== 'string' || !deviceId.trim() || deviceId.length > 500))) throw new CloudError('INVALID_ID', '任务或设备编号无效')
      const directory = parseToolDirectory(await request('/v1/agent/tools'))
      const descriptor = directory.tools.find(tool => tool.name === 'get_backup_job')
      if (!descriptor || descriptor.permission !== 'read' || descriptor.executableInCloud !== true) throw new CloudError('TOOL_NOT_ALLOWED', '该任务详情工具未获只读云端调用许可', 0, false)
      const result = parseReadToolEnvelope(await request('/v1/agent/tools/get_backup_job', 'POST', { arguments: { jobId, ...(deviceId === undefined ? {} : { deviceId }) } }), 'get_backup_job')
      const jobs = jobMatches(result.jobs)
      if (typeof result.found !== 'boolean' || result.found !== (jobs.length > 0)) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '任务查询结果与匹配列表不一致')
      const reason = result.reason === null ? null : result.reason === 'NOT_IN_SEARCH_WINDOW' ? result.reason : (() => { throw new CloudError('CLOUD_CONTRACT_MISMATCH', '任务查询范围说明无效') })()
      if (result.found && reason !== null) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '已命中任务却返回未命中原因')
      if (!result.found && reason !== 'NOT_IN_SEARCH_WINDOW') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '未命中任务必须标为超出近期查询窗口')
      return { jobs, found: result.found, searchWindow: parseSearchWindow(result.searchWindow), reason, executionAuthorized: false }
    },
    async registerDevice(displayName: string) {
      if (!displayName.trim() || displayName.length > 120) throw new CloudError('INVALID_DEVICE_NAME', '设备名称需为1至120字')
      return parseDevice(await request('/v1/devices/register', 'POST', { displayName }))
    },
    async devices() {
      const rows = await request('/v1/devices')
      if (!Array.isArray(rows)) throw new CloudError('INVALID_CLOUD_RESPONSE', '设备列表无效')
      return rows.map(parseDevice)
    },
    async getPreferences() { return preferences(await request('/v1/account/sync-preferences')) },
    async setPreferences(value: SyncPreferences) { return preferences(await request('/v1/account/sync-preferences', 'PUT', preferences(value))) },
    async ask(query: string, identity?: { sessionId: string; turnId: string }) {
      if (!query.trim() || query.length > 500) throw new CloudError('INVALID_QUERY', '请输入 1 至 500 字的查询')
      if (identity) { id(identity.sessionId); id(identity.turnId) }
      const row = object(await request('/v1/agent/turns', 'POST', { query, ...(identity ? { sessionId: identity.sessionId, turnId: identity.turnId } : {}) }))
      if (identity && (row.sessionId !== identity.sessionId || row.turnId !== identity.turnId || typeof row.persisted !== 'boolean')) throw new CloudError('TURN_NOT_CONFIRMED', '会话持久化尚未确认，请保留原编号重试')
      return parseCloudReply(row)
    },
    async modelStatus(): Promise<ModelStatus> {
      const row = object(await request('/v1/agent/model'))
      if (typeof row.configured !== 'boolean' || row.requiresExplicitConsent !== true || row.executionAuthorized !== false) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '模型配置状态无效，暂不允许发送')
      return row.configured
        ? { configured: true, provider: str(row.provider), model: str(row.model), region: str(row.region), requiresExplicitConsent: true, executionAuthorized: false }
        : { configured: false, requiresExplicitConsent: true, executionAuthorized: false }
    },
    // Model requests are separate from ordinary task search and never enter the automatic outbox.
    async askModel(query: string, identity: { sessionId: string; turnId: string }, consent: { explicitConsent: true }) {
      return submitModelTurn(query, identity, consent, false)
    },
    // Resume is a separate explicit user action using the same session/turn/query; never a fallback retry.
    async resumeModel(query: string, identity: { sessionId: string; turnId: string }, consent: { explicitConsent: true }) {
      return submitModelTurn(query, identity, consent, true)
    },
    // Caller must collect explicit user confirmation; this client never deletes automatically.
    async deleteSession(sessionId: string, confirmed: true) {
      if (confirmed !== true) throw new CloudError('CONFIRMATION_REQUIRED', '删除会话需要明确确认')
      const row = object(await request(`/v1/agent/sessions/${id(sessionId)}`, 'DELETE', { confirmed }))
      if (row.sessionId !== sessionId || row.deleted !== true || row.recreationBlocked !== true) throw new CloudError('INVALID_CLOUD_RESPONSE', '会话删除尚未确认')
    },
    // This cursor is separate from task snapshots and device sequence; bind it to the signed-in owner.
    async deletions(after = 0) {
      const row = object(await request(`/v1/agent/session-deletions?after_id=${cursor(after)}`))
      if (!Array.isArray(row.deletions) || typeof row.hasMore !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '删除记录分页无效')
      const nextCursor = cursor(row.nextCursor)
      if (nextCursor < after || (row.hasMore && nextCursor === after)) throw new CloudError('INVALID_CURSOR', '删除记录分页未前进')
      return { deletions: row.deletions.map(value => { const item = object(value); return { sessionId: str(item.sessionId), deletedAt: str(item.deletedAt) } }), nextCursor, hasMore: row.hasMore }
    },
    async invalidations(after = 0) {
      const row = object(await request(`/v1/sync/invalidations?after_id=${cursor(after)}`))
      if (!Array.isArray(row.invalidations) || typeof row.hasMore !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '失效通知分页无效')
      const nextCursor = cursor(row.nextCursor)
      if (nextCursor < after || (row.hasMore && nextCursor === after)) throw new CloudError('INVALID_CURSOR', '失效通知分页未前进')
      return { invalidations: row.invalidations.map(value => {
        const item = object(value)
        if (item.kind !== 'paths_redacted') throw new CloudError('CLOUD_CONTRACT_MISMATCH', '未知失效通知，暂不提交通知游标')
        return { kind: 'paths_redacted' as const, createdAt: str(item.createdAt) }
      }), nextCursor, hasMore: row.hasMore }
    },
    async snapshot(after = 0) {
      const row = object(await request(`/v1/sync/snapshot?after_id=${cursor(after)}&limit=100`))
      if (!Array.isArray(row.events) || typeof row.hasMore !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '任务快照分页无效')
      const nextCursor = cursor(row.nextCursor)
      if (nextCursor < after || (row.hasMore && nextCursor === after)) throw new CloudError('INVALID_CURSOR', '任务快照分页未前进')
      return { events: row.events.map(parseJobSnapshot), nextCursor, hasMore: row.hasMore }
    },
    async turns(sessionId: string, after = 0) {
      const row = object(await request(`/v1/agent/sessions/${id(sessionId)}/turns?after_id=${cursor(after)}`))
      if (!Array.isArray(row.turns) || typeof row.hasMore !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '会话分页无效')
      const nextCursor = cursor(row.nextCursor)
      if (nextCursor < after || (row.hasMore && nextCursor === after)) throw new CloudError('INVALID_CURSOR', '会话分页未前进')
      return { turns: row.turns.map(value => {
        const turn = object(value)
        return { turnId: str(turn.turnId), deviceId: str(turn.deviceId), query: str(turn.query), createdAt: str(turn.createdAt), reply: parseCloudReply(turn.response) }
      }), nextCursor, hasMore: row.hasMore }
    },
  }
}

export interface CloudJobSnapshot {
  deviceId: string
  sequence: number
  occurredAt: string
  payload: { jobId: string; name: string; state: string; source?: string; paths?: string[] }
}
function parseJobSnapshot(value: unknown): CloudJobSnapshot {
  const row = object(value), payload = object(row.payload)
  if (row.schemaVersion !== 1 || row.eventType !== 'job.snapshot.v1' || cursor(row.sequence) < 1) throw new CloudError('CLOUD_CONTRACT_MISMATCH', '任务快照版本或序号无效')
  if (payload.paths !== undefined && (!Array.isArray(payload.paths) || !payload.paths.every(p => typeof p === 'string'))) throw new CloudError('INVALID_CLOUD_RESPONSE', '任务路径列表无效')
  return { deviceId: str(row.deviceId), sequence: cursor(row.sequence), occurredAt: str(row.occurredAt), payload: { jobId: str(payload.jobId), name: str(payload.name), state: str(payload.state), ...(payload.source === undefined ? {} : { source: str(payload.source) }), ...(payload.paths === undefined ? {} : { paths: payload.paths as string[] }) } }
}
export type CloudClient = ReturnType<typeof createCloudClient>
export function createCloudModelService(client: Pick<CloudClient, 'modelStatus' | 'askModel' | 'resumeModel'>): ModelService {
  return {
    status: () => client.modelStatus(),
    ask: (query, identity, consent) => client.askModel(query, identity, consent),
    resume: (query, identity, consent) => client.resumeModel(query, identity, consent),
  }
}

export interface CloudDevice {
  deviceId: string
  displayName: string
  origin: 'desktop' | 'mobile'
  revoked: boolean
  lastSeenAt?: string
}
function parseDevice(value: unknown): CloudDevice {
  const row = object(value)
  if (!['desktop', 'mobile'].includes(str(row.origin)) || typeof row.revoked !== 'boolean') throw new CloudError('INVALID_CLOUD_RESPONSE', '设备身份字段无效')
  return { deviceId: str(row.deviceId), displayName: str(row.displayName), origin: row.origin as CloudDevice['origin'], revoked: row.revoked, ...(row.lastSeenAt === undefined ? {} : { lastSeenAt: str(row.lastSeenAt) }) }
}
