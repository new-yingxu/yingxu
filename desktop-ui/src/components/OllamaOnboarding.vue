<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { ModelService, ModelStatus } from '../api/contracts'
import { ollamaSetupView, REQUIRED_LOCAL_MODEL, REQUIRED_MODEL_PULL_COMMAND } from '../api/ollama-onboarding'

const props = defineProps<{ service?: ModelService }>()
const status = ref<ModelStatus | null>(null)
const busy = ref(false)
const failure = ref('')
const copied = ref(false)
const view = computed(() => ollamaSetupView(status.value, !!props.service && !failure.value))
let mounted = true

async function refresh() {
  if (!props.service || busy.value) return
  busy.value = true
  failure.value = ''
  status.value = null
  try {
    status.value = await props.service.status()
  } catch {
    if (mounted) failure.value = '读取本机状态失败。请确认影序桌面 Agent 服务可用后重试。'
  } finally {
    if (mounted) busy.value = false
  }
}

async function copyCommand() {
  try {
    await navigator.clipboard.writeText(REQUIRED_MODEL_PULL_COMMAND)
    copied.value = true
    window.setTimeout(() => { if (mounted) copied.value = false }, 1800)
  } catch {
    failure.value = '无法访问剪贴板，请手动复制下方命令。'
  }
}

onMounted(() => void refresh())
onUnmounted(() => { mounted = false })
</script>

<template>
  <section class="ollama-onboarding" aria-labelledby="ollama-onboarding-title">
    <header class="onboarding-heading">
      <div>
        <p class="eyebrow">本机模型 · 可选离线模式</p>
        <h3 id="ollama-onboarding-title">准备 Ollama</h3>
        <p>启用后可让 Agent 完全在本机运行。也可在上方配置 DeepSeek 或通义千问 Key，使用云端模型。</p>
      </div>
      <span class="state-pill" :data-state="view.state" role="status">{{ busy ? '正在检查…' : view.title }}</span>
    </header>

    <p v-if="failure" class="setup-error" role="alert">{{ failure }}</p>
    <p v-else class="state-detail">{{ view.detail }}</p>

    <ol class="setup-steps">
      <li>
        <div><b>手动安装并启动 Ollama</b><p>打开 Ollama 官方下载页，选择与你的电脑匹配的安装方式。安装完成后启动 Ollama 应用。</p></div>
        <a class="secondary-button" href="https://ollama.com/download" target="_blank" rel="noreferrer">打开官方下载页</a>
      </li>
      <li>
        <div><b>手动下载影序当前支持的模型</b><p>在终端执行一次以下命令。模型文件约 4.7 GB，具体下载时间取决于网络。</p></div>
        <div class="command-row"><code>{{ `ollama pull ${REQUIRED_LOCAL_MODEL}` }}</code><button class="text-button" type="button" @click="copyCommand">{{ copied ? '已复制' : '复制命令' }}</button></div>
      </li>
      <li>
        <div><b>回到影序检查状态</b><p>下载完成后刷新；显示“本机模型已就绪”即可。其他模型目前不属于桌面 Agent 的受支持配置。</p></div>
        <button class="secondary-button" type="button" :disabled="busy || !service" @click="refresh">刷新状态</button>
      </li>
    </ol>

    <aside class="hardware-note">
      <b>设备与隐私提示</b>
      <p>模型文件约 4.7 GB，运行时还需要额外内存；内存或图形资源紧张时响应可能较慢。建议先确认磁盘空间充足，并关闭暂时不用的大型应用。照片、视频、完整路径和 GPS 不会发送给 Ollama 云端服务。</p>
    </aside>
  </section>
</template>

<style scoped>
.ollama-onboarding{width:100%;max-width:100%;min-width:0;box-sizing:border-box;margin:0 0 14px;padding:18px 20px;border:1px solid var(--line);border-radius:14px;background:var(--card)}
.onboarding-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}.eyebrow{margin:0 0 4px;color:var(--muted);font-size:12px}.onboarding-heading h3{margin:0 0 6px;font-size:17px}.onboarding-heading p:last-child,.state-detail{margin:0;color:var(--muted);line-height:1.55;font-size:13px}.state-pill{padding:6px 10px;border-radius:999px;background:var(--surface,#f3f2ed);font-size:12px;font-weight:700;color:var(--text)}.state-pill[data-state=ready]{color:var(--green)}.state-pill[data-state=ollama_unavailable],.state-pill[data-state=model_not_installed]{color:var(--amber,#936b28)}.state-pill[data-state=sidecar_unavailable],.state-pill[data-state=unknown]{color:var(--red)}.state-detail,.setup-error{margin:12px 0}.setup-error{color:var(--red)}
.setup-steps{list-style:none;padding:0;margin:14px 0;display:grid;gap:0}.setup-steps li{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid var(--line)}.setup-steps li>div:first-child{flex:1;min-width:0}.setup-steps b{font-size:13px}.setup-steps p{margin:4px 0 0;color:var(--muted);font-size:12px;line-height:1.5;overflow-wrap:anywhere}.secondary-button,.text-button{flex:none}.command-row{display:flex;align-items:center;gap:12px;min-width:0;max-width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface,#f8f7f3)}.command-row code{min-width:0;overflow-wrap:anywhere;font-size:12px}.hardware-note{padding:11px 13px;border-radius:10px;background:var(--soft-green,#eef3ed);font-size:12px}.hardware-note p{margin:4px 0 0;color:var(--muted);line-height:1.55;overflow-wrap:anywhere}
@media(max-width:700px){.ollama-onboarding{padding:15px}.setup-steps li{align-items:flex-start;flex-direction:column}.command-row{width:100%;box-sizing:border-box;justify-content:space-between}.onboarding-heading{display:block}.state-pill{display:inline-block;margin-top:10px}}
</style>
