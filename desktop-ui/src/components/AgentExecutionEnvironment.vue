<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { ModelService, ModelStatus } from '../api/contracts'
import { localAgentStatusView, withLocalAgentStatusTimeout } from '../api/local-agent'

const props = defineProps<{ service?: ModelService; query: string; busy?: boolean }>()
const emit = defineEmits<{ preview: [] }>()
const status = ref<ModelStatus | null>(null)
const loading = ref(false)
const timedOut = ref(false)
const failure = ref('')
const mounted = ref(true)
const statusView = computed(() => timedOut.value
  ? { label: '本机 Agent 检查超时', detail: '系统凭据库没有及时返回。模型请求尚未发送；请处理系统提示后重启影序再检查。', ready: false, availability: 'sidecar_unavailable' as const }
  : localAgentStatusView(status.value, !!props.service))
const providerName = (provider: string) => provider === 'deepseek' ? 'DeepSeek' : provider === 'dashscope' ? '通义千问' : '本机 Ollama'
const destination = computed(() => status.value?.configured && status.value.provider !== 'ollama'
  ? `本机 LangGraph Agent 和 SQLite；模型请求直接发送给你配置的 ${providerName(status.value.provider)}，无需影序账号。`
  : '本机 LangGraph Agent、本机 SQLite 与本机 Ollama；不会向云端发送查询。')

async function refresh() {
  if (!props.service || loading.value || timedOut.value) return
  loading.value = true
  failure.value = ''
  status.value = null
  try {
    status.value = await withLocalAgentStatusTimeout(() => props.service!.status())
  } catch (error) {
    if (!mounted.value) return
    if (error && typeof error === 'object' && 'code' in error && error.code === 'LOCAL_AGENT_STATUS_TIMEOUT') timedOut.value = true
    failure.value = error instanceof Error ? error.message : '读取本机 Agent 状态失败。'
  } finally {
    if (mounted.value) loading.value = false
  }
}

onMounted(() => { mounted.value = true; void refresh() })
onUnmounted(() => { mounted.value = false })
</script>

<template>
  <section class="agent-execution-settings" aria-label="Agent 执行环境">
    <div class="agent-execution-row">
      <strong>Agent 执行环境</strong>
      <span :class="statusView.ready ? 'status-ready' : 'status-unavailable'">{{ statusView.label }}</span>
      <button class="text-button" type="button" :disabled="!service || loading || timedOut" @click="refresh">{{ loading ? '正在检查…' : '刷新状态' }}</button>
    </div>
    <p>{{ statusView.detail }}</p>
    <p v-if="statusView.ready">{{ destination }} 每轮最多 3 次模型请求、10 条脱敏摘要；不会连接影序 CloudBase 模型接口，也不会静默切换供应商。</p>
    <p v-if="failure" class="model-failure" role="alert">{{ failure }}</p>
    <button class="secondary-button compact" type="button" :disabled="!service || !statusView.ready || busy || !query.trim()" @click="emit('preview')">{{ busy ? '处理中…' : '新建模型轮次并查看发送内容' }}</button>
  </section>
</template>

<style scoped>
.agent-execution-settings{box-sizing:border-box;width:100%;min-width:0;padding:16px 18px;border:1px solid var(--line);border-radius:14px;background:var(--card);font-size:13px}
.agent-execution-row{display:flex;align-items:center;gap:12px;min-width:0;flex-wrap:wrap}.agent-execution-row strong{font-size:14px}.agent-execution-row span{flex:1;min-width:120px;font-weight:700}.agent-execution-settings p{margin:9px 0;line-height:1.6;overflow-wrap:anywhere}.agent-execution-settings .status-ready{color:var(--green)}.agent-execution-settings .status-unavailable,.agent-execution-settings .model-failure{color:var(--red)}
</style>
