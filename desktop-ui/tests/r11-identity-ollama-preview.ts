import { createApp, h, onMounted, ref } from 'vue'
import ModelConsent from '../src/components/ModelConsent.vue'
import { createCloudBaseIdentityClient } from '../src/api/cloud-identity'
import { createCloudClient, createCloudModelService } from '../src/api/cloud'
import type { AgentReply, ModelService } from '../src/api/contracts'

const env = import.meta.env as ImportMetaEnv & { VITE_R11_BASE_URL: string; VITE_R11_ACCOUNT_TOKEN: string }
const identity = createCloudBaseIdentityClient({ baseUrl: env.VITE_R11_BASE_URL, displayName: 'R11 isolated desktop', getCloudBaseAccountToken: async () => env.VITE_R11_ACCOUNT_TOKEN })
let client: ReturnType<typeof createCloudClient> | null = null
const query = '必须先调用只读工具 search_backup_jobs，只用 query="R11样本备份" 搜索；不要增加其他筛选。根据工具返回的匹配项回答任务名称和状态；未找到则明确说明未找到，不执行文件操作。'
const answer = ref<AgentReply | null>(null)
const duplicate = ref<AgentReply | null>(null)
const resumed = ref<AgentReply | null>(null)
const auth = ref('正在交换隔离测试账号短会话…')
const session = ref('')
const failure = ref('')
const renewal = ref('')
let lastTurn: { query: string; identity: { sessionId: string; turnId: string } } | null = null

const modelService: ModelService = {
  status: async () => { if (!client) throw Error('身份未就绪'); return client.modelStatus() },
  ask: async (text, turn, consent) => {
    if (!client) throw Error('身份未就绪')
    lastTurn = { query: text, identity: turn }
    return createCloudModelService(client).ask(text, turn, consent)
  },
  resume: async (text, turn, consent) => {
    if (!client) throw Error('身份未就绪')
    return createCloudModelService(client).resume!(text, turn, consent)
  },
}

createApp({
  setup() {
    onMounted(async () => {
      try {
        const current = await identity.exchange()
        session.value = `owner=${current.ownerId}; device=${current.deviceId}; origin=${current.origin}; attested=${current.deviceAttested}`
        client = createCloudClient({ baseUrl: env.VITE_R11_BASE_URL, getUserToken: identity.getUserToken })
        await client.registerDevice('R11 isolated desktop')
        const seeded = await fetch(`${env.VITE_R11_BASE_URL}/v1/sync/events`, {
          method: 'POST', headers: { Authorization: `Bearer ${await identity.getUserToken()}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ events: [{ schemaVersion: 1, eventId: 'r11-event-1', deviceId: current.deviceId, sequence: 1, eventType: 'job.snapshot.v1', occurredAt: new Date().toISOString(), payload: { jobId: 'r11-job-001', name: 'R11样本备份', state: 'success', phase: 'complete', files: 1, bytes: 1024, progress: 100, targets: [{ id: 'r11-target', state: 'success', copied: 1, skipped: 0, verified: 1, failed: 0 }] } }] }),
        })
        if (!seeded.ok) throw Error('合成任务摘要写入失败')
        await client.devices()
        auth.value = '短会话已签发；设备已注册；模型查询仅使用合成任务摘要'
      } catch (error) {
        auth.value = '身份连接失败'
        failure.value = error instanceof Error ? error.message : '本地身份测试失败'
      }
    })
    const replay = async () => {
      if (!client || !lastTurn) return
      duplicate.value = await client.askModel(lastTurn.query, lastTurn.identity, { explicitConsent: true })
    }
    const resume = async () => {
      if (!client || !lastTurn) return
      resumed.value = await client.resumeModel(lastTurn.query, lastTurn.identity, { explicitConsent: true })
    }
    const logout = async () => {
      try { await identity.logout(); auth.value = '本地短会话已清除；服务端设备已撤销'; session.value = ''; client = null }
      catch (error) { auth.value = '本地短会话已清除；服务端撤销未确认'; failure.value = error instanceof Error ? error.message : '退出未确认' }
    }
    const renew = async () => {
      try {
        const before = identity.session()
        const after = await identity.renew()
        renewal.value = `显式续期成功；同一 owner/device/origin=${before?.ownerId === after.ownerId && before?.deviceId === after.deviceId && before?.origin === after.origin}; 新到期时间=${new Date(after.expiresAt).toISOString()}`
      } catch (error) { renewal.value = `显式续期失败：${error instanceof Error ? error.message : '请重新登录账号'}` }
    }
    return () => h('main', { class: 'r11-page' }, [
      h('h1', '影序 R11 本地身份与 Ollama 联调'),
      h('p', { 'data-testid': 'auth-state' }, auth.value),
      session.value ? h('p', { 'data-testid': 'session-view' }, session.value) : null,
      client ? h('button', { 'data-testid': 'renew', onClick: renew }, '显式重新认证并续期') : null,
      renewal.value ? h('p', { 'data-testid': 'renewal-state' }, renewal.value) : null,
      failure.value ? h('p', { role: 'alert' }, failure.value) : null,
      client ? h(ModelConsent, { service: modelService, query, onReply: (value: { query: string; reply: AgentReply }) => { answer.value = value.reply } }) : null,
      answer.value ? h('section', { 'data-testid': 'answer' }, [
        h('h2', '本轮只读任务回答'), h('p', answer.value.text),
        h('p', `provider=${answer.value.provider}; model=${answer.value.model}; matches=${answer.value.matches.length}; persisted=${answer.value.historySync}`),
        h('button', { 'data-testid': 'duplicate', onClick: replay }, '重放同一已完成轮次（幂等检查）'),
        h('button', { 'data-testid': 'resume', onClick: resume }, '显式恢复同一轮次'),
        duplicate.value ? h('p', { 'data-testid': 'duplicate-result' }, `duplicate=${duplicate.value.duplicate}; provider=${duplicate.value.provider}; matches=${duplicate.value.matches.length}`) : null,
        resumed.value ? h('p', { 'data-testid': 'resume-result' }, `duplicate=${resumed.value.duplicate}; provider=${resumed.value.provider}; matches=${resumed.value.matches.length}`) : null,
        h('button', { 'data-testid': 'logout', onClick: logout }, '退出并撤销设备会话'),
      ]) : null,
    ])
  },
}).mount('#app')
