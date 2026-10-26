import { createApp, h, ref } from 'vue'
import ModelConsent from '../src/components/ModelConsent.vue'
import { createCloudClient, createCloudModelService } from '../src/api/cloud'
import type { AgentReply, ModelService } from '../src/api/contracts'

const env = import.meta.env as ImportMetaEnv & { VITE_R9_BASE_URL: string; VITE_R9_DEV_TOKEN: string }
const client = createCloudClient({
  baseUrl: env.VITE_R9_BASE_URL,
  getUserToken: async () => env.VITE_R9_DEV_TOKEN,
})
const query = '请仅用中文回复“本机 Ollama 调用成功”，并说明这是合成数据；不要调用工具，也不要声称执行了文件操作。'
const answer = ref<AgentReply | null>(null)
const replay = ref<AgentReply | null>(null)
let lastTurn: { query: string; identity: { sessionId: string; turnId: string } } | null = null
const cloudModelService = createCloudModelService(client)
/* R9 preview keeps the explicit duplicate button separate; it replays a fixed turn only for idempotency. */
const service: ModelService = {
  ...cloudModelService,
  ask: async (text, identity, consent) => {
    lastTurn = { query: text, identity }
    return cloudModelService.ask(text, identity, consent)
  },
}

createApp({
  setup() {
    const replaySameTurn = async () => {
      if (!lastTurn) return
      replay.value = await client.askModel(lastTurn.query, lastTurn.identity, { explicitConsent: true })
    }
    return () => h('main', { class: 'r9-page' }, [
      h('h1', '影序 R9 本机 Ollama 授权验证'),
      h('p', '仅合成文本与隔离开发账号；每轮需在确认框中主动批准。'),
      h(ModelConsent, {
        service,
        query,
        onReply: (value: { query: string; reply: AgentReply }) => { answer.value = value.reply },
      }),
      answer.value ? h('section', { class: 'r9-result', 'aria-label': '模型回答' }, [
        h('h2', '已完成本机模型轮次'),
        h('p', answer.value.text),
        h('p', `provider=${answer.value.provider}; model=${answer.value.model}; orchestrator=${answer.value.orchestrator}; persisted=${answer.value.historySync}`),
        h('button', { class: 'secondary-button', onClick: replaySameTurn }, '重放同一已确认轮次（验证幂等）'),
        replay.value ? h('p', `重复请求：duplicate=${replay.value.duplicate}; provider=${replay.value.provider}; model=${replay.value.model}`) : null,
      ]) : null,
    ])
  },
}).mount('#app')
