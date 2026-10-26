import { CloudError } from './cloud'
import type { ModelService, ModelStatus } from './contracts'
export interface ModelPlan {
  readonly query: string
  readonly sessionId: string
  readonly turnId: string
  readonly provider: string
  readonly model: string
  readonly region: string
}
export function modelRecoveryRequired(error: unknown): boolean {
  if (!(error instanceof CloudError)) return true
  return error.modelDispatched !== false
}
const resumableCodes = new Set([
  'MODEL_REQUEST_PENDING', 'MODEL_REQUEST_UNCERTAIN', 'MODEL_REQUEST_COMPLETED_UNRETAINED',
  'MODEL_OUTCOME_UNCERTAIN', 'CLOUD_UNREACHABLE', 'CLOUD_TIMEOUT', 'TURN_NOT_CONFIRMED',
])
function canResumeModel(error: unknown): boolean {
  return error instanceof CloudError && error.modelDispatched !== false && resumableCodes.has(error.code)
}
function isTerminalResumeFailure(error: unknown): boolean {
  return error instanceof CloudError && (
    error.status === 403 || error.status === 410 ||
    ['CHECKPOINT_NOT_FOUND', 'CHECKPOINT_SYNC_DISABLED', 'RESUME_REQUIRES_SESSION_AND_TURN'].includes(error.code)
  )
}
export function modelFailure(error: unknown, dispatched = true) {
  const record = error && typeof error === 'object' ? error as Record<string, unknown> : null
  const code = typeof record?.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(record.code)
    ? record.code
    : 'MODEL_OUTCOME_UNCERTAIN'
  if (record?.modelDispatched === false) dispatched = false
  const message = code === 'MODEL_REQUEST_PENDING' ? '本轮仍在服务端处理中。不会自动重发或新建替代轮次；请等待明确的恢复操作。'
    : code === 'MODEL_REQUEST_UNCERTAIN' ? '本轮结果未确认，可能已计费或未保存。请保留当前轮次，不要重发或新建轮次。'
    : code === 'MODEL_REQUEST_COMPLETED_UNRETAINED' ? '本轮已经处理，但回答未能保存。不会重发或新建替代轮次；请等待明确的恢复操作。'
    : code === 'CHECKPOINT_NOT_FOUND' ? '服务端未找到本轮恢复点。为避免重复调用，已停止恢复和新轮次发送。'
    : code === 'CHECKPOINT_SYNC_DISABLED' ? '当前设置未保存模型恢复点，因此无法恢复本轮；不会重发或新建替代轮次。'
    : code === 'RESUME_REQUIRES_SESSION_AND_TURN' ? '恢复请求缺少原会话或轮次编号；本轮未创建替代请求。'
    : code === 'MODEL_RECOVERY_REQUIRED' ? '本轮状态尚未解决，已阻止再次发送。请等待明确的恢复操作。'
    : code === 'MODEL_NOT_CONFIGURED' ? '模型未配置，请等待受控服务配置完成。'
    : code === 'MODEL_CONFIGURATION_CHANGED' ? '供应商或模型配置已变化，请重新核对发送范围。'
    : code === 'LOCAL_AGENT_TURN_UNCONFIRMED' ? '本机 Agent 首轮状态未确认返回。模型可能已经运行；请保留本轮，不要重发或另建轮次。'
    : code === 'LOCAL_AGENT_CONTINUATION_UNCONFIRMED' ? '本机 Agent 工具结果或后续回答未确认返回。只读检索可能已经执行；请保留本轮，不要重发或另建轮次。'
    : code === 'MODEL_CONSENT_REQUIRED' ? '请先逐次确认本次发送内容。'
    : code === 'INVALID_QUERY' ? '查询或轮次编号无效，本轮未发送。'
    : code === 'AUTH_REQUIRED' || code === 'OWNER_SESSION_STOPPED' ? '身份未连接或账号会话已停止。'
    : code === 'SESSION_DELETED' ? '会话已删除，不能重试该会话。'
    : !dispatched ? '配置状态尚未确认，本轮未发送；请刷新状态后重新核对。'
    : '请求结果未确认，可能已经计费或回答未保存。请先核对本轮状态并保留当前轮次；不会自动重发，也不要新建替代轮次。'
  const diagnosticCode = typeof record?.diagnosticCode === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(record.diagnosticCode)
    ? record.diagnosticCode
    : undefined
  return { code, message: diagnosticCode ? `${message}（本机诊断码：${diagnosticCode}）` : message, automaticRetryAllowed: false as const }
}
/** Ephemeral consent state only. Real identity bridge must bind service to one owner and
 * supply durable model-attempt records before production enablement. Never save secrets here.
 */
export function createModelConsent(service: ModelService) {
  const plans = new WeakMap<ModelPlan, 'ready' | 'sending' | 'consumed'>()
  const attempted = new Set<string>()
  let active = true
  let recoveryRequired = false
  let recoveryAvailable = false
  let recoveryPlan: ModelPlan | null = null
  let recoveryInFlight = false
  const guard = () => { if (!active) throw new CloudError('OWNER_SESSION_STOPPED', '账号会话已停止', 0, false) }
  const status = async (): Promise<ModelStatus> => { guard(); try { const value = await service.status(); guard(); return value } catch (error) { if (error instanceof CloudError) error.modelDispatched = false; throw error } }
  return {
    status,
    stop() { active = false },
    async prepare(query: string, identity: { sessionId: string; turnId: string }): Promise<ModelPlan> {
      guard()
      if (recoveryRequired) throw new CloudError('MODEL_RECOVERY_REQUIRED', '本轮状态尚未解决，已阻止再次发送', 0, false)
      if (!query.trim() || query.length > 500 || !identity.sessionId || !identity.turnId) throw new CloudError('INVALID_QUERY', '请输入有效查询和轮次编号', 0, false)
      if (attempted.has(JSON.stringify([identity.sessionId, identity.turnId]))) throw new CloudError('MODEL_REQUEST_ALREADY_SENT', '本轮已提交，禁止再次派发', 0, false)
      const configured = await status()
      if (!configured.configured) throw new CloudError('MODEL_NOT_CONFIGURED', '模型未配置', 503, false)
      const plan = Object.freeze({ query, sessionId: identity.sessionId, turnId: identity.turnId, provider: configured.provider, model: configured.model, region: configured.region })
      plans.set(plan, 'ready')
      return plan
    },
    async confirm(plan: ModelPlan, accepted: boolean) {
      guard()
      if (plans.get(plan) !== 'ready') throw new CloudError('MODEL_CONSENT_REQUIRED', '未授权或本轮已经提交', 0, false)
      plans.set(plan, 'consumed')
      if (!accepted) return null
      const key = JSON.stringify([plan.sessionId, plan.turnId])
      if (attempted.has(key)) throw new CloudError('MODEL_REQUEST_ALREADY_SENT', '本轮已提交，禁止再次派发', 0, false)
      // Reserve locally before awaiting another request to prevent double-click dispatch.
      attempted.add(key)
      const configured = await status()
      if (!configured.configured) throw new CloudError('MODEL_NOT_CONFIGURED', '模型未配置', 503, false)
      if (configured.provider !== plan.provider || configured.model !== plan.model || configured.region !== plan.region) throw new CloudError('MODEL_CONFIGURATION_CHANGED', '配置已变化，请重新确认', 0, false)
      plans.set(plan, 'sending')
      let reply: Awaited<ReturnType<ModelService['ask']>>
      try {
        reply = await service.ask(plan.query, { sessionId: plan.sessionId, turnId: plan.turnId }, { explicitConsent: true })
      } catch (error) {
        if (modelRecoveryRequired(error)) {
          recoveryRequired = true
          recoveryPlan = plan
          recoveryAvailable = !!service.resume && canResumeModel(error)
        }
        throw error
      }
      guard()
      return reply
    },
    requiresRecovery() { return recoveryRequired },
    canResume() { return recoveryAvailable && !!service.resume && !!recoveryPlan },
    getRecoveryPlan() { return recoveryPlan },
    async resume(accepted: boolean) {
      guard()
      if (accepted !== true) throw new CloudError('MODEL_CONSENT_REQUIRED', '恢复本轮需要明确确认', 0, false)
      if (recoveryInFlight) throw new CloudError('MODEL_RESUME_IN_PROGRESS', '本轮恢复请求正在处理中', 0, false)
      if (!recoveryRequired || !recoveryAvailable || !recoveryPlan || !service.resume) throw new CloudError('MODEL_RECOVERY_REQUIRED', '当前没有可用的本轮恢复操作', 0, false)
      const plan = recoveryPlan
      recoveryInFlight = true
      try {
        const configured = await status()
        if (!configured.configured) throw new CloudError('MODEL_NOT_CONFIGURED', '模型未配置', 503, false)
        if (configured.provider !== plan.provider || configured.model !== plan.model || configured.region !== plan.region) throw new CloudError('MODEL_CONFIGURATION_CHANGED', '配置已变化，请重新核对后再恢复', 0, false)
        const reply = await service.resume(plan.query, { sessionId: plan.sessionId, turnId: plan.turnId }, { explicitConsent: true })
        guard()
        recoveryRequired = false
        recoveryAvailable = false
        recoveryPlan = null
        return reply
      } catch (error) {
        if (isTerminalResumeFailure(error)) recoveryAvailable = false
        throw error
      } finally { recoveryInFlight = false }
    },
  }
}
