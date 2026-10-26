<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import type { AgentReply, ModelService, ModelStatus } from '../api/contracts'
import { createModelConsent, modelFailure, type ModelPlan } from '../api/model-consent'
const props=defineProps<{ service?: ModelService; query: string }>()
const emit=defineEmits<{ reply: [value: { query: string; reply: AgentReply }] }>()
const controller=props.service ? createModelConsent(props.service) : null
const status=ref<ModelStatus|null>(null), busy=ref(false), plan=ref<ModelPlan|null>(null)
const failure=ref<ReturnType<typeof modelFailure>|null>(null)
const recoveryRequired=ref(false)
const recoveryAvailable=ref(false), recoveryReview=ref<ModelPlan|null>(null)
const dialog=ref<HTMLElement|null>(null), cancel=ref<HTMLButtonElement|null>(null), review=ref<HTMLButtonElement|null>(null)
const recoveryDialog=ref<HTMLElement|null>(null), recoveryCancel=ref<HTMLButtonElement|null>(null)
const sessionId=crypto.randomUUID()
let mounted=true
async function refresh() {
  if (!controller || busy.value) return
  busy.value=true;failure.value=null
  try { status.value=await controller.status() } catch(error) { if(mounted)failure.value=modelFailure(error,false) } finally { if(mounted)busy.value=false }
}
async function prepare() {
  if(!controller||busy.value)return
  busy.value=true;failure.value=null
  try {
    // A fresh ID is created ONLY by this explicit user action, never in a retry handler.
    plan.value=await controller.prepare(props.query,{sessionId,turnId:crypto.randomUUID()})
    busy.value=false;await nextTick();cancel.value?.focus()
  }catch(error){if(mounted)failure.value=modelFailure(error,false)}finally{if(mounted)busy.value=false}
}
async function confirm(accepted:boolean) {
  if(!controller||!plan.value||busy.value)return
  const current=plan.value;busy.value=true
  try {const reply=await controller.confirm(current,accepted);if(reply&&mounted)emit('reply',{query:current.query,reply})}
  catch(error){if(mounted){failure.value=modelFailure(error);recoveryRequired.value=controller.requiresRecovery();recoveryAvailable.value=controller.canResume()}}
  finally{if(mounted){plan.value=null;busy.value=false;await nextTick();review.value?.focus()}}
}
async function openRecovery(){
 if(!controller||busy.value||!controller.canResume())return
 recoveryReview.value=controller.getRecoveryPlan()
 await nextTick();recoveryCancel.value?.focus()
}
async function resume(accepted:boolean){
 if(!controller||!recoveryReview.value||busy.value)return
 if(!accepted){recoveryReview.value=null;await nextTick();review.value?.focus();return}
 busy.value=true
 try{
  const reply=await controller.resume(true)
  if(reply&&mounted){recoveryRequired.value=false;recoveryAvailable.value=false;failure.value=null;emit('reply',{query:recoveryReview.value.query,reply})}
 }catch(error){if(mounted){failure.value=modelFailure(error);recoveryRequired.value=controller.requiresRecovery();recoveryAvailable.value=controller.canResume()}}
 finally{if(mounted){recoveryReview.value=null;busy.value=false;await nextTick();review.value?.focus()}}
}
function keyboard(event:KeyboardEvent){
 if(event.key==='Escape'){event.preventDefault();if(!busy.value){if(recoveryReview.value)void resume(false);else void confirm(false)}}
 if(event.key==='Tab'){
  const activeDialog=recoveryReview.value?recoveryDialog.value:dialog.value
  const nodes=Array.from(activeDialog?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')??[])
  if(!nodes.length){event.preventDefault();return}
  const current=nodes.indexOf(document.activeElement as HTMLButtonElement)
  if(event.shiftKey&&current<=0){event.preventDefault();nodes.at(-1)?.focus()}
  else if(!event.shiftKey&&current===nodes.length-1){event.preventDefault();nodes[0]?.focus()}
 }
}
onMounted(()=>void refresh())
onUnmounted(()=>{mounted=false;controller?.stop()})
</script>
<template>
 <section class="model-consent-panel" aria-label="模型服务状态">
  <div class="model-status-row"><strong>模型服务</strong><span>{{ !controller ? '未连接' : !status ? '状态待确认' : status.configured ? `${status.provider === 'ollama' && status.region === 'local' ? '本机 Ollama' : status.provider} · ${status.model} · ${status.region}` : '模型未配置' }}</span><button class="text-button" :disabled="!controller||busy" @click="refresh">刷新状态</button></div>
  <p v-if="status?.configured && status.provider === 'ollama' && status.region === 'local'">每次使用前确认本次查询及最多 10 条任务摘要；本轮最多 3 次模型请求。请求发送到本机 Ollama，不连接云端模型供应商。</p>
  <p v-else>每次使用前确认本次查询及最多 10 条任务摘要的发送范围。本轮最多 3 次模型请求；供应商可能按实际模型调用计费。密钥由受控服务保管。</p>
  <button ref="review" class="secondary-button compact" :disabled="!controller||!status?.configured||busy||recoveryRequired||!query.trim()" @click="prepare">{{ recoveryRequired ? '等待本轮恢复' : busy ? '处理中…' : '新建模型轮次并查看发送内容' }}</button>
  <button v-if="recoveryAvailable" class="secondary-button compact" :disabled="busy" @click="openRecovery">{{ busy ? '处理中…' : '查看并恢复本轮' }}</button>
  <p v-if="failure" role="alert" class="model-failure">[{{ failure.code }}] {{ failure.message }}</p>
 </section>
 <div v-if="plan" class="model-consent-overlay" @keydown="keyboard">
  <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="model-consent-title" class="model-consent-dialog">
   <h2 id="model-consent-title">{{ plan.provider === 'ollama' && plan.region === 'local' ? '确认本次发送到本机 Ollama' : '确认本次发送到模型供应商' }}</h2>
   <dl><dt>供应商 / 模型 / 区域</dt><dd>{{ plan.provider === 'ollama' && plan.region === 'local' ? '本机 Ollama' : plan.provider }} / {{ plan.model }} / {{ plan.region }}</dd><dt>数据目的地</dt><dd>{{ plan.provider === 'ollama' && plan.region === 'local' ? '本机 127.0.0.1:11434' : '受控模型供应商' }}</dd><dt>本次查询</dt><dd class="model-query">{{ plan.query }}</dd><dt>附带数据与调用上限</dt><dd>最多 10 条已同步任务摘要（任务名称、编号、状态及时间）。本轮最多 3 次模型请求。不附带照片、文件路径或 GPS 字段；请核对本次输入内容。</dd><dt>发送与权限</dt><dd>{{ plan.provider === 'ollama' && plan.region === 'local' ? '请求只发送到本机 Ollama，不连接云端模型供应商；本机服务仍可能按本轮最多 3 次请求执行。' : '供应商可能按实际模型调用次数计费，本轮最多 3 次模型请求。' }}只读工具仅查询任务摘要，不获得文件执行权限；回答不授权文件操作。失败或结果不确定时不自动重发。</dd></dl>
   <div class="model-consent-actions"><button ref="cancel" class="secondary-button" :disabled="busy" @click="confirm(false)">取消，不发送</button><button class="primary-button" :disabled="busy" @click="confirm(true)">{{ busy ? '等待本轮结果…' : plan.provider === 'ollama' && plan.region === 'local' ? '确认发送到本机' : '确认本次发送' }}</button></div>
  </section>
 </div>
 <div v-if="recoveryReview" class="model-consent-overlay" @keydown="keyboard">
  <section ref="recoveryDialog" role="dialog" aria-modal="true" aria-labelledby="model-recovery-title" class="model-consent-dialog">
   <h2 id="model-recovery-title">确认恢复同一轮次</h2>
   <dl><dt>本次查询</dt><dd class="model-query">{{ recoveryReview.query }}</dd><dt>恢复范围</dt><dd>向服务端发送 resume:true，继续使用原会话和轮次编号；不会创建新轮次，也不会自动重发。本轮最多 3 次模型请求；恢复可能继续尚未完成的模型处理，新增用量取决于服务端本轮状态和供应商计费规则。</dd><dt>发送与权限</dt><dd>本请求只用于恢复当前轮次。只读工具只查询任务摘要，不获得文件执行权限。若服务端无法恢复，将显示明确错误并保持新轮次锁定。</dd></dl>
   <div class="model-consent-actions"><button ref="recoveryCancel" class="secondary-button" :disabled="busy" @click="resume(false)">取消</button><button class="primary-button" :disabled="busy" @click="resume(true)">{{ busy ? '正在请求恢复…' : '确认恢复本轮' }}</button></div>
  </section>
 </div>
</template>
<style scoped>
.model-consent-panel{padding:14px 16px;margin:12px 0;border:1px solid var(--line);border-radius:12px;background:var(--card);font-size:13px}
.model-status-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.model-status-row span{flex:1;min-width:120px}.model-consent-panel p{margin:8px 0;line-height:1.6}.model-failure{color:var(--red);overflow-wrap:anywhere}
.model-consent-overlay{position:fixed;inset:0;background:#17291fc0;z-index:140;display:grid;place-items:center;padding:20px}.model-consent-dialog{width:min(680px,100%);max-height:calc(100dvh - 40px);overflow:auto;padding:24px;border-radius:20px;background:var(--card);box-shadow:0 18px 80px #0004}.model-consent-dialog h2{margin:0 0 16px}.model-consent-dialog dt{font-weight:700;margin:12px 0 6px}.model-consent-dialog dd{margin:0;overflow-wrap:anywhere;line-height:1.65}.model-query{white-space:pre-wrap}.model-consent-actions{display:flex;gap:12px;justify-content:flex-end;position:sticky;bottom:-24px;background:var(--card);padding:16px 0 0}.model-consent-actions button{min-width:0}
@media(max-width:800px){.model-consent-dialog{padding:18px}.model-consent-actions{bottom:-18px;flex-wrap:wrap}}
</style>
