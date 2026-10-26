<script setup lang="ts">
import { onMounted,onUnmounted,ref } from 'vue'
import type { NetdiskApi,NetdiskProvider,NetdiskTask } from '../api/contracts'
import { uploadState,uploadChunks } from '../api/netdisk-view'
import { errorText } from '../api/tauri'
const props=defineProps<{api:NetdiskApi}>()
const providers=ref<NetdiskProvider[]>([]),tasks=ref<NetdiskTask[]>([]),error=ref(''),loading=ref(false),running=ref<string[]>([])
const auth=ref<{provider:string;url:string;expiresAt:number;state:string}|null>(null)
let alive=true,refreshing=false
async function refresh(){if(refreshing)return;refreshing=true;try{const [p,t]=await Promise.all([props.api.providers(),props.api.tasks()]);if(alive){providers.value=p.filter(row=>["baidu","quark"].includes(row.id));tasks.value=t}}catch(e){if(alive)error.value=errorText(e)}finally{refreshing=false}}
async function action(work:()=>Promise<unknown>){loading.value=true;error.value='';try{await work();await refresh()}catch(e){error.value=errorText(e)}finally{loading.value=false}}
async function connect(provider:string){await action(async()=>{const a=await props.api.beginAuth(provider);auth.value={provider,url:a.authorizeUrl,expiresAt:Date.now()+a.expiresInSeconds*1000,state:'pending'}})}
async function poll(){if(!auth.value)return;if(Date.now()>auth.value.expiresAt){auth.value=null;error.value='[AUTH_EXPIRED] 授权窗口已过期，请重新连接';return}await action(async()=>{const r=await props.api.authPoll(auth.value!.provider);auth.value!.state=r.state;if(r.error)error.value=`[${r.error}] 授权未完成`})}
async function complete(){if(!auth.value)return;await action(async()=>{await props.api.completeAuth(auth.value!.provider);auth.value=null})}
async function copyLink(){if(!auth.value)return;try{await navigator.clipboard.writeText(auth.value.url)}catch{error.value='[CLIPBOARD_UNAVAILABLE] 无法复制，请检查系统剪贴板权限'}}
async function run(task:NetdiskTask){if(running.value.includes(task.taskId))return;running.value.push(task.taskId);error.value='';try{await props.api.run(task.taskId)}catch(e){error.value=errorText(e)}finally{running.value=running.value.filter(id=>id!==task.taskId);await refresh()}}
const allowed=(id:string)=>providers.value.some(p=>p.id===id&&p.approval==='approved'&&p.authorized&&p.realUploadAvailable)
onMounted(()=>void refresh());const timer=window.setInterval(()=>{if(!document.hidden&&running.value.length)void refresh()},1000)
onUnmounted(()=>{alive=false;clearInterval(timer);auth.value=null})
</script>
<template><div class="netdisk-native"><div class="panel-heading"><div><h3>个人网盘授权与上传队列</h3><p>百度按实际资质与授权状态启用；夸克无公开 API。排队不等于已上传。</p></div><button class="secondary-button compact" :disabled="loading" @click="refresh">刷新网盘状态</button></div>
 <div v-for="p in providers" :key="p.id" class="provider-row"><span><b>{{p.display}}</b><small>{{p.approval!=='approved' ? p.approval==='no_public_api'?'无公开第三方 API':p.authorized?'资质未获批；检测到既有授权，上传未启用':'资质未获批' : p.authorized?'本机已授权（系统安全存储）':'资质已获批，尚未授权'}}</small></span><button v-if="p.approval==='approved'&&p.realUploadAvailable&&!p.authorized" class="secondary-button compact" :disabled="loading" @click="connect(p.id)">连接个人网盘</button><button v-if="p.authorized" class="text-button" :disabled="loading" @click="action(()=>api.unbind(p.id))">解除本机授权</button></div>
 <section v-if="auth" class="netdisk-auth"><p>请在系统浏览器完成官方授权；不在此输入密码或授权码。链接仅本轮临时保留。</p><button class="secondary-button compact" @click="copyLink">复制授权链接，在系统浏览器打开</button><button class="secondary-button compact" :disabled="loading" @click="poll">检查授权结果</button><button v-if="auth.state==='ready'" class="primary-button compact" :disabled="loading" @click="complete">完成授权保存</button><small>状态：{{auth.state}}</small></section>
 <p v-if="error" role="alert" class="netdisk-error">{{error}}</p>
 <p v-if="!tasks.length" class="table-foot">没有本机上传任务。可在资料库为已校验的本地副本排队，上传还需确认。</p>
 <article v-for="task in tasks" :key="task.taskId" class="netdisk-task"><div><b>{{task.name}}</b><small>{{uploadState(task)}} · {{uploadChunks(task)}}</small><small v-if="task.error">{{typeof task.error==='string'?task.error:`[${task.error.code}] ${task.error.message??''}`}}</small></div><div class="netdisk-actions"><button v-if="task.state!=='completed'" class="primary-button compact" :disabled="!allowed(task.provider)||running.includes(task.taskId)" @click="run(task)">{{running.includes(task.taskId)?'上传中…':'上传 / 续传'}}</button><button v-if="!['completed','cancelled'].includes(task.state)" class="secondary-button compact" @click="action(()=>api.cancel(task.taskId))">停止任务</button></div></article>
</div></template>
<style scoped>.netdisk-native{margin-bottom:16px}.netdisk-native .provider-row{justify-content:space-between}.netdisk-auth{padding:12px;border:1px solid var(--line);border-radius:12px;display:flex;gap:8px;flex-wrap:wrap}.netdisk-auth p{width:100%;margin:0 0 6px}.netdisk-error{color:var(--red);overflow-wrap:anywhere}.netdisk-task{display:flex;justify-content:space-between;gap:14px;align-items:center;border-top:1px solid var(--line);padding:14px 0}.netdisk-task div:first-child{min-width:0;overflow-wrap:anywhere}.netdisk-task small{display:block;color:var(--muted);margin-top:6px}.netdisk-actions{display:flex;gap:8px;flex-wrap:wrap}@media(max-width:800px){.netdisk-task{align-items:flex-start;flex-direction:column}}</style>
