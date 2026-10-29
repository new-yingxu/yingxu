<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { ModelProviderConfigService } from '../api/contracts'

const props = defineProps<{ service?: ModelProviderConfigService }>()
const state = ref<{ configured: boolean; provider: 'ollama'|'dashscope'|'deepseek'; model: string; localModel?: string; cloudKeyStored?: boolean } | null>(null)
const provider = ref<'dashscope'|'deepseek'>('dashscope')
const model = ref('qwen-plus')
const apiKey = ref('')
const busy = ref(false)
const notice = ref('')
const failure = ref('')
const providerName = (value: string) => value === 'deepseek' ? 'DeepSeek' : value === 'dashscope' ? '通义千问' : '本机 Ollama'

async function refresh() {
  if (!props.service) return
  busy.value = true; failure.value = ''; notice.value = ''
  try {
    state.value = await props.service.status()
    if (state.value.provider === 'dashscope' || state.value.provider === 'deepseek') {
      provider.value = state.value.provider
      model.value = state.value.model
    }
  } catch (error) {
    failure.value = error instanceof Error && error.message.startsWith('DESKTOP_STATUS_TIMEOUT:')
      ? error.message.slice('DESKTOP_STATUS_TIMEOUT: '.length)
      : '读取系统钥匙串中的模型设置失败。'
  }
  finally { busy.value = false }
}
function chooseProvider(value: 'dashscope'|'deepseek') {
  provider.value = value
  model.value = value === 'dashscope' ? 'qwen-plus' : 'deepseek-flash'
}
async function save() {
  if (!props.service || busy.value) return
  failure.value = ''; notice.value = ''
  if (!model.value.trim() || !apiKey.value.trim()) { failure.value = '请填写模型名称和 API Key。'; return }
  busy.value = true
  try {
    state.value = await props.service.save(provider.value, model.value.trim(), apiKey.value.trim())
    apiKey.value = ''
    notice.value = '已保存到系统钥匙串。本机 Agent 已切换到该云端模型；不会创建影序账号或启用影序云同步。保存时没有发送测试请求。'
  } catch { failure.value = '保存失败。请检查系统钥匙串是否可用，密钥未发送到影序云端。' }
  finally { busy.value = false }
}
async function remove() {
  if (!props.service || busy.value) return
  if (!window.confirm('移除系统钥匙串中的云端模型 Key，并恢复本机 Ollama？')) return
  busy.value = true; failure.value = ''; notice.value = ''
  try { state.value = await props.service.remove(); provider.value = 'dashscope'; model.value = 'qwen-plus'; notice.value = '已移除云端模型 Key；Agent 将恢复为本机 Ollama。' }
  catch { failure.value = '无法从系统钥匙串移除模型 Key。' }
  finally { busy.value = false }
}
onMounted(() => void refresh())
</script>

<template>
  <section v-if="service" class="provider-settings" aria-labelledby="byok-title">
    <div class="provider-heading"><div><p class="eyebrow">无需影序账号 · 用户自带 API Key</p><h3 id="byok-title">云端模型</h3><p>Agent 编排、资料库检索和文件操作仍在本机；模型请求由本机直接发送给 DeepSeek 或通义千问。</p></div><button class="text-button" :disabled="busy" @click="refresh">刷新</button></div>
    <p class="provider-state" role="status">{{ busy ? '处理中…' : state?.configured && state.provider !== 'ollama' ? `当前已配置：${providerName(state.provider)} · ${state.model}（密钥在系统钥匙串）` : state?.cloudKeyStored ? `当前使用本机模型 ${state.localModel || 'Ollama'}；云端 Key 已保留，保存云端配置后即可切换。` : `当前使用本机模型 ${state?.localModel || 'Ollama'}；尚未配置云端 Key。` }}</p>
    <div class="provider-form">
      <label><span>模型平台</span><select :value="provider" :disabled="busy" @change="chooseProvider(($event.target as HTMLSelectElement).value as 'dashscope'|'deepseek')"><option value="deepseek">DeepSeek 开放平台</option><option value="dashscope">通义千问（阿里云百炼）</option></select></label>
      <label><span>模型名称</span><input v-model="model" autocomplete="off" :disabled="busy" :placeholder="provider === 'deepseek' ? 'deepseek-flash' : 'qwen-plus'"></label>
      <label class="key-field"><span>API Key</span><input v-model="apiKey" type="password" autocomplete="new-password" :disabled="busy" placeholder="粘贴你的 API Key；不会保存到浏览器或影序云端"></label>
      <button class="primary-button" :disabled="busy || !apiKey.trim() || !model.trim()" @click="save">{{ busy ? '保存中…' : '保存并启用' }}</button>
    </div>
    <p v-if="failure" class="provider-error" role="alert">{{ failure }}</p><p v-else-if="notice" class="provider-notice" role="status">{{ notice }}</p>
    <button v-if="state?.configured && state.provider !== 'ollama'" class="text-button remove-key" :disabled="busy" @click="remove">移除 Key 并恢复本机 Ollama</button>
    <p class="provider-privacy">保存配置不会发送测试请求。每轮运行前逐次确认：查询文字和必要的脱敏索引摘要会发送到所选平台；原片、完整路径和 GPS 不会发送。模型用量按供应商规则计费。文件操作在本机执行，仍需单独确认。</p>
  </section>
</template>

<style scoped>
.provider-settings{box-sizing:border-box;width:100%;min-width:0;margin:0 0 14px;padding:18px 20px;border:1px solid var(--line);border-radius:14px;background:var(--card)}.provider-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.provider-heading h3{margin:0 0 5px;font-size:17px}.provider-heading p:last-child,.provider-privacy{margin:0;color:var(--muted);font-size:13px;line-height:1.55;overflow-wrap:anywhere}.eyebrow{margin:0 0 4px;color:var(--muted);font-size:12px}.provider-state{margin:12px 0;color:var(--green);font-size:13px;font-weight:650}.provider-form{display:grid;grid-template-columns:minmax(150px,.8fr) minmax(150px,.8fr) minmax(220px,1.4fr) auto;gap:10px;align-items:end}.provider-form label{display:grid;gap:5px;min-width:0;font-size:12px;font-weight:650}.provider-form input,.provider-form select{box-sizing:border-box;width:100%;min-width:0;height:38px;padding:0 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface,#f8f7f3);color:var(--text);font:inherit}.provider-form button{white-space:nowrap}.provider-error{color:var(--red)}.provider-notice{color:var(--green)}.remove-key{margin:8px 0}.provider-privacy{margin-top:12px}@media(max-width:850px){.provider-form{grid-template-columns:1fr 1fr}.provider-form .key-field{grid-column:1/-1}.provider-form button{justify-self:start}}@media(max-width:520px){.provider-settings{padding:15px}.provider-form{grid-template-columns:1fr}}
</style>
