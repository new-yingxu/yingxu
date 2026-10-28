<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { invoke, isTauri } from '@tauri-apps/api/core'

type Task = { taskId:string; localPath:string; remotePath:string; bytes:number; sha256:string; state:string; error?:string; chunksDone:number; chunksTotal:number; verifiedRemote:boolean; verification?:string; speedBytesPerSecond?:number; remainingSeconds?:number }
type Replica = { id:string; path:string; bytes?:number; sha256:string }
type Status = { authorized:boolean; expiresAt?:number; appFolder?:string }
const native = isTauri()
const status = ref<Status>({authorized:false})
const tasks = ref<Task[]>([])
const replicas = ref<Replica[]>([])
const selected = ref('')
const prepared = ref<Task | null>(null)
const error = ref('')
const message = ref('')
const busy = ref(false)
const running = ref(false)
const waiting = ref(false)
const disconnectConfirm = ref(false)
const accountName = ref('')
let timer:ReturnType<typeof setTimeout> | undefined
let progressTimer:ReturnType<typeof setInterval> | undefined
let mounted = true
const selectedReplica = computed(() => replicas.value.find(r=>r.id===selected.value))
const stateLabels:Record<string,string> = { queued:'等待确认', uploading:'上传中', failed:'上传未完成', committing:'正在核对合并结果', commit_uncertain:'结果待核对', completed:'已上传并核对', cancelled:'已取消' }
const errorMessages:Record<string,string> = { BAIDU_NETWORK_UNAVAILABLE:'网络连接未完成，可稍后继续上传。', BAIDU_HTTP_ERROR:'百度网盘服务暂不可用，可稍后重试。', BAIDU_TOKEN_EXPIRED:'百度授权已过期，请重新授权后继续。', BAIDU_PERMISSION_DENIED:'此百度应用尚未获得所需网盘权限。', LOCAL_HASH_MISMATCH:'本地备份副本发生变化，已停止上传。', BAIDU_REMOTE_HASH_MISMATCH:'网盘返回的文件信息不一致，请核对远端结果。', BAIDU_COMMIT_UNCERTAIN:'合并结果尚未确定，请点击核对结果。' }
function taskError(code:string) { return errorMessages[code]||'上传尚未完成，请核对连接和网盘权限。' }
function request<T>(action:string,args:Record<string,unknown>={}):Promise<T> {
  return invoke<T>('baidu_request',{ action,replicaId:null,taskId:null,confirmed:false,...args })
}
function bytes(value:number) { return value>=1024**3 ? `${(value/1024**3).toFixed(2)} GB` : `${(value/1024**2).toFixed(2)} MB` }
function progress(task:Task) { return task.chunksTotal ? Math.min(100,Math.round(task.chunksDone/task.chunksTotal*100)) : 0 }
async function refresh() {
  if (!native) return
  const [nextStatus,nextTasks]=await Promise.all([request<Status>('status'),request<Task[]>('tasks')])
  if (mounted) {status.value=nextStatus;tasks.value=nextTasks}
}
async function perform(fn:()=>Promise<void>) {
  if (busy.value) return
  busy.value=true;error.value='';message.value=''
  try { await fn() } catch (e) { error.value=String(e) } finally { busy.value=false }
}
async function connect() {
  await perform(async()=>{
    await request('beginAuth');waiting.value=true;message.value='请在系统浏览器完成百度账号授权，再返回影序。'
    poll()
  })
}
function poll() {
  timer=setTimeout(async()=>{
    if (!mounted||!waiting.value) return
    try {
      const result=await request<{state:string}>('pollAuth')
      if (result.state==='authorized') {waiting.value=false;message.value='百度网盘已连接。';await refresh()}
      else poll()
    } catch(e) {waiting.value=false;error.value=String(e)}
  },1500)
}
async function authorizeFolder() {
  await perform(async()=>{
    const chosen=await invoke<unknown>('select_directory',{purpose:'destination'})
    if (!chosen) return
    replicas.value=await request<Replica[]>('replicas')
    selected.value=replicas.value[0]?.id||''
    message.value=replicas.value.length?'请选择一个已校验副本，预览后确认上传。':'该目录中没有已校验的备份副本。请先在影序完成本地备份。'
  })
}
async function prepare() {
  if (!selectedReplica.value) return
  await perform(async()=>{prepared.value=await request<Task>('prepare',{replicaId:selected.value,taskId:crypto.randomUUID()});await refresh()})
}
async function run() {
  if (!prepared.value||running.value) return
  const taskId=prepared.value.taskId
  prepared.value=null;running.value=true;error.value='';message.value='后台上传已开始，可以继续使用其他页面。'
  progressTimer=setInterval(()=>{request<Task[]>('tasks').then(t=>{if(mounted)tasks.value=t}).catch(()=>{})},1500)
  try {
    const result=await request<Task>('run',{taskId,confirmed:true})
    if(result.error)error.value=`${taskError(result.error)}本地副本保留。`
    else message.value=result.state==='completed'?'上传完成，网盘 MD5 与文件大小核对一致。':`任务状态：${stateLabels[result.state]||result.state}`
    await refresh()
  }catch(e){error.value=String(e)}finally{running.value=false;if(progressTimer)clearInterval(progressTimer)}
}
async function cancel(task:Task) { await perform(async()=>{await request('cancel',{taskId:task.taskId});message.value='已请求停止后续分片；远端已上传内容不会自动删除。';await refresh()}) }
async function disconnect() {await perform(async()=>{await request('disconnect',{confirmed:true});disconnectConfirm.value=false;status.value={authorized:false};accountName.value='';message.value='已移除本机百度凭据。网盘文件保留；应用授权可在百度账号中管理。'})}
async function verifyAccount(){await perform(async()=>{const result=await request<{baiduName?:string;netdiskName?:string}>('verifyAccount');accountName.value=result.netdiskName||result.baiduName||'已验证的百度账号';message.value='账号查询已通过。上传权限将在实际上传时验证。'})}
onMounted(()=>{if(native)perform(refresh)})
onUnmounted(()=>{mounted=false;if(timer)clearTimeout(timer);if(progressTimer)clearInterval(progressTimer)})
</script>

<template>
  <div class="baidu-panel">
    <div class="baidu-header"><div><b>百度网盘</b><small>{{ native ? (status.authorized ? `已连接${accountName?' · '+accountName:''}` : '尚未连接个人百度网盘') : '请在影序桌面端连接个人百度网盘' }}</small></div><span class="state-pill" :class="status.authorized?'success':'queued'">{{ status.authorized?'已连接':'待连接' }}</span></div>
    <p class="baidu-note">先完成本地备份校验，再上传所选副本。影像直接发送至你的百度网盘；本地原件保留。</p>
    <div class="baidu-buttons"><button class="secondary-button compact" :disabled="!native||busy||waiting||running" @click="connect">{{ waiting?'等待浏览器授权…':status.authorized?'重新授权':'连接百度网盘' }}</button><button v-if="status.authorized" class="secondary-button compact" :disabled="busy||running" @click="verifyAccount">核对账号</button><button v-if="status.authorized" class="text-button" :disabled="busy||running||waiting" @click="disconnectConfirm=true">断开连接</button></div>
    <p v-if="message" class="baidu-message" role="status">{{ message }}</p><p v-if="error" class="baidu-error" role="alert">{{ error }}</p>
    <template v-if="status.authorized">
      <div class="baidu-buttons"><button class="secondary-button compact" :disabled="busy||running" @click="authorizeFolder">选择已备份目录</button><button class="text-button" :disabled="busy" @click="perform(refresh)">刷新任务</button></div>
      <label v-if="replicas.length" class="baidu-select"><span>选择已通过 SHA-256 校验的副本</span><select v-model="selected" class="select-field" :disabled="busy||running"><option v-for="replica in replicas" :key="replica.id" :value="replica.id">{{ replica.path }}</option></select></label>
      <button v-if="selectedReplica" class="secondary-button compact" :disabled="busy||running" @click="prepare">预览上传</button>
      <div v-if="prepared" class="baidu-confirm"><b>{{ prepared.state==='commit_uncertain'?'核对远端结果':'确认上传副本' }}</b><dl><dt>本地副本</dt><dd>{{ prepared.localPath }}</dd><dt>百度网盘目标</dt><dd>{{ prepared.remotePath }}</dd><dt>大小</dt><dd>{{ bytes(prepared.bytes) }}</dd></dl><p>目标文件使用独立任务编号命名，保留网盘现有文件。</p><div class="baidu-buttons"><button class="primary-button compact" :disabled="running" @click="run">{{ prepared.state==='commit_uncertain'?'仅查询核对结果':'确认上传 / 继续' }}</button><button class="secondary-button compact" @click="prepared=null">稍后处理</button></div></div>
      <div v-if="tasks.length" class="baidu-tasks"><h4>本机上传记录</h4><article v-for="task in tasks" :key="task.taskId" class="baidu-task"><b>{{ stateLabels[task.state]||task.state }}</b><span class="baidu-path">{{ task.remotePath }}</span><progress :value="progress(task)" max="100" :aria-label="`上传进度 ${progress(task)}%`"></progress><small>{{ progress(task) }}% · {{ bytes(task.bytes) }} · {{ task.verification==='provider_md5_and_size'?'MD5 与大小已核对':'远端结果待核对' }}</small><small v-if="task.state==='uploading' && task.speedBytesPerSecond">{{ bytes(task.speedBytesPerSecond) }}/s · 预计剩余 {{ task.remainingSeconds ?? '—' }} 秒</small><small v-if="task.error" class="baidu-error">{{ taskError(task.error) }}</small><div class="baidu-buttons"><button v-if="!['completed','cancelled'].includes(task.state)" class="text-button" :disabled="running||busy" @click="prepared=task">{{ ['committing','commit_uncertain'].includes(task.state)?'核对结果':'继续 / 重试' }}</button><button v-if="['queued','uploading','failed'].includes(task.state)" class="text-button" :disabled="busy" @click="cancel(task)">取消</button></div></article></div>
    </template>
    <div v-if="disconnectConfirm" class="baidu-confirm"><b>断开本机百度网盘连接？</b><p>将移除本机凭据。网盘文件和上传记录保留。</p><div class="baidu-buttons"><button class="secondary-button compact" :disabled="busy" @click="disconnect">确认断开</button><button class="text-button" @click="disconnectConfirm=false">返回</button></div></div>
  </div>
</template>

<style scoped>
.baidu-panel{min-width:0;padding:8px 0}.baidu-header{display:flex;gap:12px;align-items:center;justify-content:space-between}.baidu-header>div{display:grid;gap:6px;min-width:0}.baidu-header small,.baidu-note,.baidu-message,.baidu-task small{color:var(--muted);font-size:12px;line-height:1.6}.baidu-note{margin:12px 0}.baidu-buttons{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:10px 0}.baidu-error{color:#a44235;font-size:12px;overflow-wrap:anywhere;line-height:1.6}.baidu-message{overflow-wrap:anywhere}.baidu-select{display:grid;gap:7px;margin:14px 0;font-size:12px}.baidu-select select{max-width:100%;width:100%;min-width:0}.baidu-confirm{background:var(--page);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:14px;min-width:0}.baidu-confirm dl{display:grid;grid-template-columns:85px minmax(0,1fr);gap:8px;font-size:12px;line-height:1.6}.baidu-confirm dd{margin:0;overflow-wrap:anywhere}.baidu-confirm dt{color:var(--muted)}.baidu-confirm p{font-size:12px;line-height:1.6}.baidu-tasks h4{font-size:13px;margin-bottom:4px}.baidu-task{display:grid;gap:7px;border-top:1px solid var(--line);padding:14px 0;font-size:12px}.baidu-path{overflow-wrap:anywhere;word-break:break-word;line-height:1.6}.baidu-task progress{width:100%;height:8px;accent-color:var(--green)}.baidu-task .baidu-buttons{margin:0}@media(max-width:800px){.baidu-confirm dl{grid-template-columns:1fr}.baidu-confirm dt{margin-top:4px}}
</style>
