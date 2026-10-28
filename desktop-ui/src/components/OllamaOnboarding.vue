<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { ModelProviderConfigService, ModelService, ModelStatus } from '../api/contracts'

const props = defineProps<{ service?: ModelService; providerConfig?: ModelProviderConfigService }>()
const status = ref<ModelStatus | null>(null)
const available = ref(false)
const models = ref<string[]>([])
const selectedModel = ref('')
const busy = ref(false)
const failure = ref('')
const notice = ref('')
const connected = computed(() => !!props.service && !failure.value)
const configuredStatus = computed(() => status.value?.configured ? status.value : null)
const statusText = computed(() => {
  if (!connected.value) return '本机 Agent 服务暂不可用'
  if (!configuredStatus.value) return '尚未配置可用的 Agent 模型'
  if (configuredStatus.value.provider !== 'ollama') return `当前运行：${configuredStatus.value.provider === 'deepseek' ? 'DeepSeek' : '通义千问'} · ${configuredStatus.value.model}`
  if (configuredStatus.value.availability === 'ready') return `本机模型已就绪 · ${configuredStatus.value.model}`
  if (configuredStatus.value.availability === 'ollama_unavailable') return '尚未检测到 Ollama'
  return `本机模型未就绪 · ${configuredStatus.value.model || '等待选择'}`
})
let mounted = true

async function refresh() {
  if (!props.service || busy.value) return
  busy.value = true
  failure.value = ''
  notice.value = ''
  try {
    const [nextStatus, catalog] = await Promise.all([props.service.status(), props.service.listLocalModels()])
    if (!mounted) return
    status.value = nextStatus
    available.value = catalog.available
    models.value = catalog.models
    const current = nextStatus.configured && nextStatus.provider === 'ollama' ? nextStatus.model : props.providerConfig ? (await props.providerConfig.status()).localModel : ''
    selectedModel.value = current && catalog.models.includes(current) ? current : catalog.models[0] || ''
  } catch {
    if (mounted) failure.value = '读取 Ollama 模型列表失败。请确认本机 Ollama 已启动，再刷新。'
  } finally {
    if (mounted) busy.value = false
  }
}

async function useSelectedModel() {
  if (!props.providerConfig || !selectedModel.value || busy.value) return
  busy.value = true
  failure.value = ''
  notice.value = ''
  try {
    const selected = await props.providerConfig.selectLocalModel(selectedModel.value)
    if (!props.service) return
    status.value = await props.service.status()
    notice.value = `已切换到本机模型 ${selected.model}。切换没有发送模型请求；运行 Agent 时仍会逐次请求确认。`
  } catch {
    failure.value = '切换本机模型失败。请刷新后重试；已有云端 Key 不会被删除。'
  } finally {
    busy.value = false
  }
}

onMounted(() => void refresh())
onUnmounted(() => { mounted = false })
</script>

<template>
  <section class="ollama-settings" aria-labelledby="ollama-settings-title">
    <header class="ollama-heading">
      <div>
        <p class="eyebrow">本机运行 · 无需 API Key</p>
        <h3 id="ollama-settings-title">本机模型</h3>
        <p>影序会读取本机 Ollama 已安装的模型。模型选择保存在系统凭据库；Agent 查询和文件操作仍在本机执行。</p>
      </div>
      <button class="text-button" type="button" :disabled="busy" @click="refresh">{{ busy ? '正在检查…' : '刷新列表' }}</button>
    </header>

    <p class="ollama-status" role="status">{{ statusText }}</p>
    <p v-if="failure" class="ollama-message error-note" role="alert">{{ failure }}</p>
    <p v-else-if="notice" class="ollama-message success-note" role="status">{{ notice }}</p>

    <div v-if="available && models.length" class="model-picker">
      <label for="local-model-select">已安装模型</label>
      <div class="model-picker-row">
        <select id="local-model-select" v-model="selectedModel" :disabled="busy">
          <option v-for="model in models" :key="model" :value="model">{{ model }}</option>
        </select>
        <button class="primary-button" type="button" :disabled="busy || !selectedModel || (configuredStatus?.provider === 'ollama' && configuredStatus.model === selectedModel)" @click="useSelectedModel">{{ busy ? '切换中…' : '设为当前模型' }}</button>
      </div>
      <p>共检测到 {{ models.length }} 个本机模型。选择模型不会触发推理请求；点击“设为当前模型”后，新 Agent 轮次将使用所选模型。</p>
    </div>
    <div v-else class="ollama-empty">
      <b>{{ available ? '尚未安装本机模型' : 'Ollama 当前不可用' }}</b>
      <p>安装并启动 Ollama 后刷新。你也可以在终端运行 <code>ollama pull qwen2.5:7b</code> 下载默认模型；影序不会自动下载安装。</p>
      <a class="secondary-button" href="https://ollama.com/download" target="_blank" rel="noreferrer">打开 Ollama 官方下载页</a>
    </div>

    <aside class="hardware-note">
      <b>设备与隐私</b>
      <p>本机模型会占用磁盘和内存。照片、视频、完整路径和 GPS 不会发送给 Ollama 云端服务；运行 Agent 仍需逐次确认。</p>
    </aside>
  </section>
</template>

<style scoped>
.ollama-settings{box-sizing:border-box;width:100%;min-width:0;padding:18px 20px;border:1px solid var(--line);border-radius:14px;background:var(--card)}
.ollama-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.ollama-heading h3{margin:0 0 5px;font-size:17px}.ollama-heading p:last-child{margin:0;color:var(--muted);font-size:13px;line-height:1.55;overflow-wrap:anywhere}.eyebrow{margin:0 0 4px;color:var(--muted);font-size:12px}.ollama-status{margin:14px 0;color:var(--green);font-size:13px;font-weight:650}.model-picker{display:grid;gap:7px;padding:14px 0;border-top:1px solid var(--line)}.model-picker>label{font-size:12px;font-weight:650}.model-picker-row{display:flex;gap:10px;align-items:center}.model-picker select{height:40px;flex:1;min-width:0;padding:0 10px;border:1px solid var(--line);border-radius:9px;background:var(--surface,#f8f7f3);color:var(--text);font:inherit}.model-picker>p,.ollama-empty p{margin:0;color:var(--muted);font-size:12px;line-height:1.5;overflow-wrap:anywhere}.ollama-empty{display:grid;justify-items:start;gap:9px;padding:14px 0;border-top:1px solid var(--line)}.ollama-empty>b{font-size:13px}.ollama-message{font-size:12px;line-height:1.5}.hardware-note{margin-top:14px;padding:12px 14px;border-radius:10px;background:var(--soft-green,#eef3ed);font-size:12px}.hardware-note p{margin:4px 0 0;color:var(--muted);line-height:1.5}.error-note{color:var(--red)}.success-note{color:var(--green)}
@media(max-width:620px){.ollama-settings{padding:15px}.ollama-heading{flex-direction:column}.model-picker-row{align-items:stretch;flex-direction:column}.model-picker-row button{align-self:flex-start}}
</style>
