import { CloudError } from './cloud'
import type { AgentMatch, AgentReply, Confirmation, LocalAgentAvailability, ModelService, ModelStatus, MediaKind } from './contracts'

const CAPABILITIES = ['search_media', 'get_metadata', 'get_backup_job', 'open_item'] as const
const ALLOWED_ARGUMENTS: Record<string, readonly string[]> = {
  search_media: ['query', 'kind', 'quality', 'limit'],
  get_metadata: ['mediaId'],
  get_backup_job: ['jobId'],
  open_item: ['mediaId'],
}
const MAX_RESPONSE_TOOLS = 2
const MAX_TURN_TOOLS = 10
const MAX_MODEL_REQUESTS = 3

function localTransportFailure(stage: 'TURN' | 'CONTINUATION', error: unknown): CloudError {
  const diagnosticCode = error && typeof error === 'object' && 'code' in error &&
    typeof error.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(error.code)
    ? error.code
    : undefined
  const failure = new CloudError(
    `LOCAL_AGENT_${stage}_UNCONFIRMED`,
    stage === 'TURN'
      ? '本机 Agent 首轮状态未确认返回。模型可能已经运行；请保留本轮，不要重发或另建轮次。'
      : '本机 Agent 工具结果或后续回答未确认返回。只读检索可能已经执行；请保留本轮，不要重发或另建轮次。',
    0,
    false,
  )
  // Expose only a bounded symbolic code, never a raw IPC error or payload.
  failure.modelDispatched = true
  failure.diagnosticCode = diagnosticCode
  return failure
}

type Json = Record<string, unknown>
export interface LocalTurnRequest {
  query: string
  sessionId: string
  turnId: string
  useModel: true
  capabilities: readonly string[]
  destinationGrantIds: []
}
export interface LocalToolRequest {
  callId: string
  tool: string
  arguments: unknown
  sessionId: string
  turnId: string
  scope: 'current_desktop_device'
  expiresAt: string
  executionAuthorized: false
  requiresLocalConfirmation: boolean
}
export interface LocalToolResult {
  callId: string
  tool: string
  status: 'succeeded' | 'failed' | 'declined'
  data: Json
}
export interface LocalTurnResponse {
  status: 'awaiting_local_tools' | 'awaiting_local_confirmation' | 'completed'
  sessionId: string
  turnId: string
  query: string
  orchestrator: 'langgraph'
  executionAuthorized: false
  filesChanged: false
  modelRequests: number
  localToolRequests?: LocalToolRequest[]
  continuationToken?: string
  response?: {
    answer: string
    matches: unknown[]
    model: string
    provider: string
    usage?: unknown
    executionAuthorized: false
    filesChanged: false
  }
  proposal?: {
    actionId: string
    tool: string
    fileIds: string[]
    requestedName?: string
    destinationGrantId?: string
    expiresAt: string
  }
}
export interface LocalAgentTransport {
  /** Native IPC keeps the sidecar bearer token inside the Rust host. */
  status(): Promise<ModelStatus>
  startTurn(request: LocalTurnRequest): Promise<LocalTurnResponse>
  submitResults(sessionId: string, turnId: string, request: {
    continuationToken: string
    results: LocalToolResult[]
    useModel: true
  }): Promise<LocalTurnResponse>
}
export interface LocalOpenPlan {
  actionId: string
  tool: 'open_item'
  fileId: string
  name: string
  /** Host preview expiry, epoch milliseconds. Matches the Tauri `agent_prepare_action` reply. */
  expiresAtMs: number
}
export interface LocalActionReceipt {
  actionId: string
  tool: 'open_item'
  status: 'succeeded' | 'declined' | 'failed'
  data: Json
  executionAuthorized: false
}
/** The host accepts a preview only inside this window (5 minutes). */
export const ACTION_WINDOW_MS = 5 * 60 * 1000
/** Where the pending open proposal came from. Shown on the confirmation card. */
export const OPEN_PROPOSAL_SOURCE = '本机 Agent 提议'

export interface LocalAgentStatusView {
  label: string
  detail: string
  ready: boolean
  availability: LocalAgentAvailability | 'unknown'
}

/** A hanging native status IPC must not leave the Agent panel loading forever. */
export function withLocalAgentStatusTimeout<T>(read: () => Promise<T>, timeoutMs = 25_000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new CloudError(
      'LOCAL_AGENT_STATUS_TIMEOUT',
      '本机 Agent 状态查询超时，尚未发起模型调用。为避免重复访问系统凭据库，本次已停止自动检查。',
      0,
      false,
    )), timeoutMs)
  })
  return Promise.race([Promise.resolve().then(read), timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer)
  })
}

/** Runtime health is reported by status(); this notice only covers a missing native adapter. */
export function shouldShowLocalAgentAdapterNotice(mode: 'native' | 'mock', hasModelAdapter: boolean): boolean {
  return mode === 'native' && !hasModelAdapter
}

/** User-facing local runtime state; unavailable states never suggest cloud fallback. */
export function localAgentStatusView(status: ModelStatus | null, connected = true): LocalAgentStatusView {
  if (!connected || !status) return { label: '状态待确认', detail: '无法读取本机 Agent 状态。刷新后再试；不会切换到云端模型。', ready: false, availability: 'unknown' }
  switch (status.availability) {
    case 'ready': return { label: `已就绪${status.configured ? ` · ${status.model}` : ''}`, detail: '本机 Agent 与 Ollama 可用。每次运行前仍需你确认。', ready: status.configured, availability: 'ready' }
    case 'configured': return { label: `云端模型已配置${status.configured ? ` · ${status.model}` : ''}`, detail: `本机 Agent 已连接 ${status.configured ? status.provider : '用户选择的供应商'}。模型密钥保存在系统钥匙串；发送查询前会展示数据范围并逐次确认。`, ready: status.configured, availability: 'configured' }
    case 'model_not_installed': return { label: '模型未安装', detail: `本机 Ollama 正在运行，但没有找到所需模型${status.configured ? `：${status.model}` : ''}。安装模型后刷新状态。`, ready: false, availability: 'model_not_installed' }
    case 'ollama_unavailable': return { label: 'Ollama 未运行', detail: '未检测到本机 Ollama 服务。启动 Ollama 后刷新状态。不会自动改用云端模型。', ready: false, availability: 'ollama_unavailable' }
    case 'sidecar_unavailable': return { label: '本机 Agent 服务不可用', detail: '影序本机 Agent 未连接。请重启应用后刷新状态；不会自动改用云端模型。', ready: false, availability: 'sidecar_unavailable' }
    default: return { label: status.configured ? `${status.provider} · ${status.model}` : '本机模型不可用', detail: status.configured ? '模型状态已读取。' : '本机模型未配置；不会自动改用云端模型。', ready: status.configured, availability: 'unknown' }
  }
}

function object(value: unknown): Json {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 响应结构无效', 0, false)
  return value as Json
}
function exactKeys(row: Json, allowed: readonly string[], label: string): void {
  if (Object.keys(row).some(key => !allowed.includes(key))) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', `${label}包含未允许的字段`, 0, false)
}
function nonEmpty(value: unknown, label: string, max = 160): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', `${label}无效`, 0, false)
  return value
}
function verifiedOpenReceipt(raw: unknown, actionId: string, mediaId: string, accepted: boolean): { receipt: LocalActionReceipt; data: Json } {
  try {
    const row = object(raw)
    exactKeys(row, ['actionId', 'tool', 'status', 'data', 'executionAuthorized'], '本机操作回执')
    if (row.actionId !== actionId || row.tool !== 'open_item' || row.executionAuthorized !== false || !['succeeded', 'declined', 'failed'].includes(String(row.status))) throw new Error('invalid receipt envelope')
    const data = object(row.data)
    if (row.status === 'succeeded') {
      exactKeys(data, ['mediaId', 'opened', 'receiptId'], '打开素材回执')
      if (!accepted || data.mediaId !== mediaId || data.opened !== true) throw new Error('success receipt does not match the confirmed action')
      nonEmpty(data.receiptId, '操作回执编号')
    } else if (Object.keys(data).length) throw new Error('failed receipt contains success data')
    return { receipt: row as unknown as LocalActionReceipt, data }
  } catch {
    throw new CloudError('OPEN_RECEIPT_INVALID', '本机没有返回有效的打开回执，不能确认素材已打开。', 0, false)
  }
}
function parseMatches(value: unknown): AgentMatch[] {
  if (!Array.isArray(value) || value.length > 10) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 匹配列表无效', 0, false)
  return value.map(raw => {
    const item = object(raw)
    if (item.kind !== 'local_media') throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 只允许返回本机影像结果', 0, false)
    const media = object(item.media)
    exactKeys(media, ['mediaId', 'name', 'kind', 'bytes', 'qualityState'], '影像摘要')
    if (!['photo', 'video', 'other'].includes(String(media.kind)) || !['good', 'review', 'unanalyzed'].includes(String(media.qualityState)) || !Number.isSafeInteger(media.bytes) || (media.bytes as number) < 0) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机影像摘要字段无效', 0, false)
    return { kind: 'local_media', media: { mediaId: nonEmpty(media.mediaId, '素材编号'), name: nonEmpty(media.name, '素材名称', 500), kind: media.kind as MediaKind, bytes: media.bytes as number, qualityState: media.qualityState as 'good' | 'review' | 'unanalyzed' } }
  })
}
function parseTurn(value: unknown, sessionId: string, turnId: string): LocalTurnResponse {
  const row = object(value)
  if (row.sessionId !== sessionId || row.turnId !== turnId || row.orchestrator !== 'langgraph' || row.executionAuthorized !== false || row.filesChanged !== false || !Number.isSafeInteger(row.modelRequests) || (row.modelRequests as number) < 1 || (row.modelRequests as number) > MAX_MODEL_REQUESTS) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 轮次边界或标识无效', 0, false)
  if (!['awaiting_local_tools', 'awaiting_local_confirmation', 'completed'].includes(String(row.status))) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 返回了未知状态', 0, false)
  return row as unknown as LocalTurnResponse
}
function parseContinuation(value: unknown, previous: LocalTurnResponse): LocalTurnResponse {
  const next = parseTurn(value, previous.sessionId, previous.turnId)
  if (next.modelRequests !== previous.modelRequests + 1) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 续交没有按轮次递增模型请求计数', 0, false)
  return next
}
function parseArguments(tool: string, raw: unknown): Json {
  const args = object(raw)
  const allowed = ALLOWED_ARGUMENTS[tool]
  if (!allowed) throw new CloudError('AGENT_TOOL_NOT_ALLOWED', '本机 Agent 请求了未允许的工具', 0, false)
  exactKeys(args, allowed, '工具参数')
  if (tool === 'search_media') {
    if (typeof args.query !== 'string' || !args.query.trim() || args.query.length > 500) throw new CloudError('INVALID_AGENT_TOOL_ARGS', '搜索条件无效', 0, false)
    if (args.kind !== undefined && !['all', 'photo', 'video', 'other'].includes(String(args.kind))) throw new CloudError('INVALID_AGENT_TOOL_ARGS', '素材类型无效', 0, false)
    if (args.quality !== undefined && !['all', 'good', 'review', 'unanalyzed'].includes(String(args.quality))) throw new CloudError('INVALID_AGENT_TOOL_ARGS', '质量筛选无效', 0, false)
    if (args.limit !== undefined && (!Number.isSafeInteger(args.limit) || (args.limit as number) < 1 || (args.limit as number) > 10)) throw new CloudError('INVALID_AGENT_TOOL_ARGS', '结果数量需为 1 至 10', 0, false)
  } else if (tool === 'get_metadata' || tool === 'open_item') nonEmpty(args.mediaId, '素材编号')
  else nonEmpty(args.jobId, '任务编号')
  return args
}
function pathlessToolData(tool: string, raw: unknown): Json {
  const envelope = object(raw)
  if (envelope.executionAuthorized !== false) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机工具返回了未授权执行状态', 0, false)
  if (tool === 'search_media') {
    exactKeys(envelope, ['tool', 'scope', 'query', 'matches', 'truncated', 'executionAuthorized'], '素材搜索结果')
    if (envelope.tool !== tool || envelope.scope !== 'current_device' || typeof envelope.truncated !== 'boolean' || !Array.isArray(envelope.matches) || envelope.matches.length > 10) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机素材搜索结果无效', 0, false)
    // The local service already binds this result to the pending query. Keep
    // the query out of the continuation payload; the server contract accepts
    // only the bounded, pathless media summaries and truncation flag.
    nonEmpty(envelope.query, '查询条件', 500)
    return { truncated: envelope.truncated, matches: parseMatches(envelope.matches.map(media => ({ kind: 'local_media', media }))).map(match => match.kind === 'local_media' ? match.media : null) }
  }
  if (tool === 'get_metadata') {
    exactKeys(envelope, ['tool', 'scope', 'media', 'executionAuthorized'], '元数据结果')
    const media = object(envelope.media)
    exactKeys(media, ['mediaId', 'name', 'kind', 'bytes', 'qualityState', 'metadata'], '影像元数据')
    const metadata = object(media.metadata)
    exactKeys(metadata, ['width', 'height', 'durationMs'], '脱敏元数据')
    if (envelope.tool !== tool || envelope.scope !== 'current_device') throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机元数据结果无效', 0, false)
    return { media: { mediaId: nonEmpty(media.mediaId, '素材编号'), name: nonEmpty(media.name, '素材名称', 500), kind: media.kind, bytes: media.bytes, qualityState: media.qualityState, metadata } }
  }
  if (tool === 'get_backup_job') {
    exactKeys(envelope, ['tool', 'scope', 'job', 'executionAuthorized'], '任务结果')
    const job = object(envelope.job)
    exactKeys(job, ['jobId', 'name', 'kind', 'state', 'phase', 'createdAt', 'files', 'totalBytes', 'progress', 'verify', 'targets'], '脱敏任务摘要')
    if (envelope.tool !== tool || envelope.scope !== 'current_device' || !Array.isArray(job.targets) || job.targets.length > 10) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机任务结果无效', 0, false)
    const targets = job.targets.map(rawTarget => { const target = object(rawTarget); exactKeys(target, ['state', 'copied', 'skipped', 'verified', 'failed'], '目的地任务摘要'); return target })
    return { job: { ...job, targets } }
  }
  throw new CloudError('AGENT_TOOL_NOT_ALLOWED', '本机 Agent 结果不属于只读工具', 0, false)
}
function responseReply(response: LocalTurnResponse): AgentReply {
  const row = object(response.response)
  if (typeof row.answer !== 'string' || row.answer.length > 8000 || typeof row.model !== 'string' || !row.model.trim() || typeof row.provider !== 'string' || !row.provider.trim() || row.executionAuthorized !== false || row.filesChanged !== false) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 回答无效', 0, false)
  const matches = parseMatches(row.matches)
  return { text: row.answer, matches, orchestrator: 'langgraph', provider: row.provider, model: row.model, historySync: 'not_synced' }
}

export function createLocalAgentService(options: {
  transport: LocalAgentTransport
  executeTool: (tool: string, args: Json) => Promise<unknown>
  prepareAction: (actionId: string, fileId: string, expiresAtMs: number) => Promise<LocalOpenPlan>
  confirmAction: (actionId: string, accepted: boolean) => Promise<LocalActionReceipt>
  confirmModelUse: (value: Confirmation) => Promise<boolean>
  now?: () => number
}) {
  const now = options.now ?? Date.now
  const attempted = new Set<string>()
  const pending = new Map<string, { request: LocalToolRequest; plan: LocalOpenPlan; query: string; sessionId: string; turnId: string; continuationToken: string; priorResults: LocalToolResult[]; toolsUsed: number; modelRequests: number }>()

  async function status(): Promise<ModelStatus> {
    const value = await options.transport.status()
    if (value.requiresExplicitConsent !== true || value.executionAuthorized !== false) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机模型状态缺少逐次确认边界', 0, false)
    if (!['ready', 'configured', 'ollama_unavailable', 'model_not_installed', 'sidecar_unavailable'].includes(String(value.availability)) || value.cloudFallback !== false)
      throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 状态缺少可用性或云端回退边界', 0, false)
    if (value.configured !== ['ready', 'configured'].includes(String(value.availability)))
      throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机 Agent 可用性与配置状态不一致', 0, false)
    if (value.configured && !((value.provider === 'ollama' && value.region === 'local') || (value.provider === 'dashscope' && value.region === 'beijing') || (value.provider === 'deepseek' && value.region === 'global'))) throw new CloudError('LOCAL_PROVIDER_NOT_ALLOWED', '桌面 Agent 模型供应商配置无效；不会改用其他服务', 0, false)
    return value
  }

  /** Mirrors the host rule: a preview is only valid inside the next 5 minutes. */
  function actionExpiryMs(value: unknown, label: string): number {
    const parsed = typeof value === 'string' ? Date.parse(value) : Number.NaN
    if (!Number.isFinite(parsed)) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', `${label}无效`, 0, false)
    const current = now()
    if (parsed <= current || parsed > current + ACTION_WINDOW_MS)
      throw new CloudError('ACTION_EXPIRED', '本机操作提议已过期，请重新查询', 0, false)
    return parsed
  }

  /** prepare → 用户确认 → confirm 的第一步：向宿主换取一次性预览。 */
  async function prepareOpenPlan(actionId: string, fileId: string, expiresAt: unknown): Promise<LocalOpenPlan> {
    const expiresAtMs = actionExpiryMs(expiresAt, '本机操作提议有效期')
    const plan = await options.prepareAction(actionId, fileId, expiresAtMs)
    exactKeys(plan as unknown as Json, ['actionId', 'tool', 'fileId', 'name', 'expiresAtMs'], '本机打开计划')
    if (plan.actionId !== actionId || plan.tool !== 'open_item' || plan.fileId !== fileId)
      throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机打开计划与提议素材不一致', 0, false)
    if (!Number.isSafeInteger(plan.expiresAtMs) || plan.expiresAtMs <= now() || plan.expiresAtMs > now() + ACTION_WINDOW_MS)
      throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机打开计划的有效期无效', 0, false)
    nonEmpty(plan.name, '素材名称', 500)
    return plan
  }

  async function run(query: string, identity: { sessionId: string; turnId: string }, alreadyConsented: boolean, toolsUsed = 0): Promise<AgentReply> {
    if (!query.trim() || query.length > 500) throw new CloudError('INVALID_QUERY', '请输入 1 至 500 字的查询', 0, false)
    const sessionId = nonEmpty(identity.sessionId, '会话编号')
    const turnId = nonEmpty(identity.turnId, '轮次编号')
    const key = JSON.stringify([sessionId, turnId])
    if (attempted.has(key)) throw new CloudError('MODEL_REQUEST_ALREADY_SENT', '本轮已提交，不能重复派发', 0, false)
    const configured = await status()
    if (!configured.configured) throw new CloudError('LOCAL_AGENT_UNAVAILABLE', 'Agent 模型未配置；不会切换到其他服务', 503, false)
    const localModel = configured.provider === 'ollama'
    const destination = localModel ? '本机 LangGraph Agent 与本机 Ollama；查询不会发到云端。' : `本机 LangGraph Agent 与 ${configured.provider} 云端模型；查询文字及最多 10 条脱敏索引摘要会发送给该供应商。`
    if (!alreadyConsented && !(await options.confirmModelUse({ title: localModel ? '确认在本机运行 Agent' : '确认发送到自带 Key 云端模型', description: `${destination}照片/视频原片、完整路径和 GPS 不会发送；文件操作仍由本机执行并单独确认。本轮最多 3 次模型请求。`, rows: [{ label: '模型', value: `${configured.provider} · ${configured.model}` }, { label: '查询', value: query.trim() }, { label: '本地工具', value: '影像检索、脱敏元数据、备份任务；打开素材需另行确认' }], confirmLabel: localModel ? '确认本机运行' : '确认发送' }))) throw new CloudError('USER_CANCELLED', '已取消，本机 Agent 未收到查询', 0, false)
    const current = await status()
    if (!current.configured) throw new CloudError('LOCAL_AGENT_UNAVAILABLE', 'Agent 模型已不可用；本轮未发送', 0, false)
    if (current.provider !== configured.provider || current.model !== configured.model || current.region !== configured.region) throw new CloudError('MODEL_CONFIGURATION_CHANGED', '本机模型配置在确认期间发生变化；本轮未发送，请重新核对', 0, false)
    attempted.add(key)
    const request: LocalTurnRequest = { query: query.trim(), sessionId, turnId, useModel: true, capabilities: CAPABILITIES, destinationGrantIds: [] }
    let rawFirst: LocalTurnResponse
    try { rawFirst = await options.transport.startTurn(request) }
    catch (error) { throw localTransportFailure('TURN', error) }
    const first = parseTurn(rawFirst, sessionId, turnId)
    return processResponse(first, query.trim(), toolsUsed)
  }

  async function processResponse(response: LocalTurnResponse, query: string, toolsUsed: number): Promise<AgentReply> {
    if (response.status === 'completed') return responseReply(response)
    if (response.status === 'awaiting_local_confirmation') {
      const proposal = object(response.proposal)
      exactKeys(proposal, ['actionId', 'tool', 'fileIds', 'requestedName', 'destinationGrantId', 'expiresAt'], '本机操作提议')
      if (proposal.destinationGrantId !== undefined) throw new CloudError('AGENT_TOOL_NOT_ALLOWED', '当前本机 Agent 不接受目标目录授权提议', 0, false)
      if (proposal.tool !== 'open_item' || !Array.isArray(proposal.fileIds) || proposal.fileIds.length !== 1 || typeof proposal.expiresAt !== 'string') {
        const reply = responseReply(response)
        return { ...reply, text: `${reply.text}\n此操作当前没有可用的本机执行桥，尚未执行。` }
      }
      const actionId = nonEmpty(proposal.actionId, '操作编号')
      const fileId = nonEmpty(proposal.fileIds[0], '素材编号')
      const plan = await prepareOpenPlan(actionId, fileId, proposal.expiresAt)
      pending.set(actionId, { request: { callId: actionId, tool: 'open_item', arguments: { mediaId: fileId }, sessionId: response.sessionId, turnId: response.turnId, scope: 'current_desktop_device', expiresAt: new Date(plan.expiresAtMs).toISOString(), executionAuthorized: false, requiresLocalConfirmation: true }, plan, query, sessionId: response.sessionId, turnId: response.turnId, continuationToken: '', priorResults: [], toolsUsed, modelRequests: response.modelRequests })
      const reply = responseReply(response)
      return { ...reply, proposedAction: { actionId, tool: 'open_item', fileIds: [fileId], expiresAt: new Date(plan.expiresAtMs).toISOString(), source: OPEN_PROPOSAL_SOURCE, kind: 'open', label: plan.name, target: '本机默认应用', objects: [plan.name] } }
    }
    if (response.status !== 'awaiting_local_tools' || !response.continuationToken || !Array.isArray(response.localToolRequests) || response.localToolRequests.length < 1 || response.localToolRequests.length > MAX_RESPONSE_TOOLS || response.modelRequests < 1 || response.modelRequests >= MAX_MODEL_REQUESTS) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机工具续交状态无效或模型请求预算不足', 0, false)
    const continuationToken = nonEmpty(response.continuationToken, '续交令牌', 4096)
    const reads: LocalToolResult[] = []
    const openRequests: LocalToolRequest[] = []
    const callIds = new Set<string>()
    if (toolsUsed + response.localToolRequests.length > MAX_TURN_TOOLS) throw new CloudError('LOCAL_TOOL_LIMIT', '本轮达到 10 次本机工具调用上限', 0, true)
    for (const rawRequest of response.localToolRequests) {
      const req = object(rawRequest) as unknown as LocalToolRequest
      exactKeys(req as unknown as Json, ['callId', 'tool', 'arguments', 'sessionId', 'turnId', 'scope', 'expiresAt', 'executionAuthorized', 'requiresLocalConfirmation'], '本机工具请求')
      const callId = nonEmpty(req.callId, '工具调用编号')
      if (callIds.has(callId)) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机工具请求包含重复编号', 0, false)
      callIds.add(callId)
      if (req.sessionId !== response.sessionId || req.turnId !== response.turnId || req.scope !== 'current_desktop_device' || req.executionAuthorized !== false || typeof req.requiresLocalConfirmation !== 'boolean' || typeof req.expiresAt !== 'string' || !Number.isFinite(Date.parse(req.expiresAt)) || Date.parse(req.expiresAt) <= now()) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '本机工具请求的轮次、范围或有效期无效', 0, false)
      const args = parseArguments(req.tool, req.arguments)
      if (req.tool === 'open_item') {
        if (!req.requiresLocalConfirmation) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '打开素材必须要求本机明确确认', 0, false)
        openRequests.push(req)
      } else {
        if (req.requiresLocalConfirmation) throw new CloudError('LOCAL_AGENT_CONTRACT_MISMATCH', '当前只读工具不使用写操作确认协议', 0, false)
        try { reads.push({ callId, tool: req.tool, status: 'succeeded', data: pathlessToolData(req.tool, await options.executeTool(req.tool, args)) }) }
        catch { reads.push({ callId, tool: req.tool, status: 'failed', data: {} }) }
      }
    }
    if (toolsUsed + reads.length + openRequests.length > MAX_TURN_TOOLS) throw new CloudError('LOCAL_TOOL_LIMIT', '本轮达到 10 次本机工具调用上限', 0, true)
    if (openRequests.length > 1) throw new CloudError('MULTIPLE_PENDING_ACTIONS', '每轮只允许确认一个打开素材请求', 0, false)
    if (openRequests.length) {
      const req = openRequests[0]
      const args = parseArguments('open_item', req.arguments)
      const actionId = nonEmpty(req.callId, '工具调用编号')
      const fileId = nonEmpty(args.mediaId, '素材编号')
      const plan = await prepareOpenPlan(actionId, fileId, req.expiresAt)
      pending.set(actionId, { request: req, plan, query, sessionId: response.sessionId, turnId: response.turnId, continuationToken, priorResults: reads, toolsUsed: toolsUsed + reads.length, modelRequests: response.modelRequests })
      const text = response.response?.answer || `找到“${plan.name}”。请确认后才会通过本机默认应用打开。`
      const replyFields = response.response ? responseReply(response) : null
      return { text, matches: replyFields?.matches ?? [], orchestrator: 'langgraph', provider: replyFields?.provider ?? 'ollama', ...(replyFields?.model ? { model: replyFields.model } : {}), historySync: 'not_synced', proposedAction: { actionId, tool: 'open_item', fileIds: [fileId], expiresAt: new Date(plan.expiresAtMs).toISOString(), source: OPEN_PROPOSAL_SOURCE, kind: 'open', label: plan.name, target: '本机默认应用', objects: [plan.name] } }
    }
    const results: LocalToolResult[] = [...reads]
    if (toolsUsed + results.length > MAX_TURN_TOOLS) throw new CloudError('LOCAL_TOOL_LIMIT', '本轮达到 10 次本机工具调用上限', 0, true)
    let rawNext: LocalTurnResponse
    try {
      rawNext = await options.transport.submitResults(response.sessionId, response.turnId, { continuationToken, results, useModel: true })
    } catch (error) { throw localTransportFailure('CONTINUATION', error) }
    const next = parseContinuation(rawNext, response)
    return processResponse(next, query, toolsUsed + results.length)
  }

  const model: ModelService = { status, ask: (query, identity, consent) => run(query, identity, consent.explicitConsent) }
  return {
    model,
    ask: (query: string) => run(query, { sessionId: crypto.randomUUID(), turnId: crypto.randomUUID() }, false),
    async confirm(actionId: string, accepted: boolean): Promise<string | AgentReply> {
      const context = pending.get(actionId)
      if (!context) throw new CloudError('ACTION_EXPIRED', '本机操作提议不存在或已处理，请重新查询', 0, false)
      pending.delete(actionId)
      if (context.plan.expiresAtMs <= now()) throw new CloudError('ACTION_EXPIRED', '本机操作提议已过期，请重新查询', 0, false)
      const rawReceipt = await options.confirmAction(actionId, accepted)
      const { receipt, data: receiptData } = verifiedOpenReceipt(rawReceipt, actionId, context.plan.fileId, accepted)
      if (!context.continuationToken) return receipt.status === 'succeeded'
        ? `已通过本机默认应用打开 ${context.plan.name}。`
        : receipt.status === 'declined'
          ? '已取消本机操作，素材未打开。'
          : '本机默认应用未能打开该素材；素材未修改。'
      const result: LocalToolResult = { callId: context.request.callId, tool: 'open_item', status: receipt.status, data: receipt.status === 'succeeded' ? receiptData : {} }
      const next = parseContinuation(await options.transport.submitResults(context.sessionId, context.turnId, { continuationToken: context.continuationToken, results: [...context.priorResults, result], useModel: true }), { status: 'awaiting_local_tools', sessionId: context.sessionId, turnId: context.turnId, query: context.query, orchestrator: 'langgraph', executionAuthorized: false, filesChanged: false, modelRequests: context.modelRequests })
      return processResponse(next, context.query, context.toolsUsed + 1)
    },
  }
}
