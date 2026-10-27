<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { AgentReply, AppSettings, ArchiveTask, AuditResult, Device, MediaFile, NamePreset, TaskState, UserSession } from './api/contracts'
import { createMockApi } from './api/mock'
import { isNativeTauriRuntime, passwordSignIn, signOut as nativeSignOut } from './api/nativeAuth'
import { Activity, Archive, ArrowDownToLine, ArrowRight, Bell, Check, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Clock3, Cloud, Copy, Database, FileImage, FileVideo, Filter, Folder, HardDrive, Image, LayoutDashboard, LockKeyhole, Moon, MoreHorizontal, Play, Plus, Search, Send, Settings2, ShieldCheck, Sparkles, Sun, WandSparkles, X } from './icons'

const api = createMockApi()
const nativeTauriRuntime = isNativeTauriRuntime()
type Page = 'home' | 'backup' | 'transfer' | 'library' | 'history' | 'task' | 'agent' | 'account' | 'presets' | 'health' | 'delivery' | 'settings'
const page = ref<Page>('home')
type DesktopAuthState = { kind: 'mock'; user: UserSession } | { kind: 'cloudbase-provider'; subject: string; expiresIn: number; identifier: string }
const authState = ref<DesktopAuthState | null>(null)
const activeDisplayName = computed(() => authState.value?.kind === 'mock' ? authState.value.user.displayName : authState.value?.kind === 'cloudbase-provider' ? authState.value.identifier : '')
const activeEmail = computed(() => authState.value?.kind === 'mock' ? authState.value.user.email : authState.value?.kind === 'cloudbase-provider' ? (authState.value.identifier.includes('@') ? authState.value.identifier : '') : '')
const loginMode = ref<'wechat' | 'phone-code' | 'email-code' | 'email-password'>(nativeTauriRuntime ? 'email-password' : 'wechat')
const loginIdentifier = ref('')
const loginProof = ref('')
const loginError = ref('')
const loginBusy = ref(false)
const notice = ref('')
const showModal = ref('')
const globalSearchOpen = ref(false)
const globalSearchQuery = ref('')
const query = ref('')
const filterKind = ref<'all' | 'photo' | 'video' | 'other'>('all')
const filterQuality = ref('all')
const filterState = ref<TaskState | 'all'>('all')
const selectedTaskId = ref('YX-20250422-014')
const taskDetail = ref<ArchiveTask | null>(null)
const tasks = ref<ArchiveTask[]>([])
const files = ref<MediaFile[]>([])
const sources = ref<{id:string;name:string;path:string;files:number;size:string;free:string}[]>([])
const devices = ref<Device[]>([])
const presets = ref<NamePreset[]>([])
const auditResult = ref<AuditResult | null>(null)
const scanBusy = ref(false)
const transferBusy = ref(false)
const transferFiles = ref<MediaFile[]>([])
const selectedTransfer = ref<string[]>([])
const transferConfirmSource = ref(false)
const transferTarget = ref('本机硬盘 / 老内容归档')
const backupStep = ref(1)
const backupSource = ref('sd-1')
const backupTargets = ref<string[]>(['local'])
const backupPreset = ref('preset-camera')
const backupTask = ref<ArchiveTask | null>(null)
const newPresetName = ref('')
const agentInput = ref('')
const agentMessages = ref<{role:'assistant'|'user';text:string;reply?:AgentReply;actionId?:string}[]>([])
const settings = reactive<AppSettings>({ theme: 'light', startOnLogin: true, backgroundOnClose: true, verify: true, autoAnalyze: true, similarity: 88, maxUploadMbps: 0, syncHistory: true, syncAgent: true, syncThumbnails: true, syncPaths: false, webhookEnabled: false, webhookUrl: '', aiProvider: '本地规则优先' })
const deliveryName = ref('客户交付包 · 春日人像')
const deliveryContact = ref(true)
const deliveryManifest = ref(true)
const deliverySelection = ref<string[]>(['f1', 'f3'])
const summary = reactive({ fileCount: 0, totalSize: '', destinations: 0, reviewCount: 0 })
const navGroups = [
  { title: '工作区', items: [
    { id: 'home' as Page, label: '工作台', icon: LayoutDashboard },
    { id: 'backup' as Page, label: '复制新内容', icon: ArrowDownToLine },
    { id: 'transfer' as Page, label: '转移老内容', icon: Folder },
    { id: 'library' as Page, label: '影像资料库', icon: Image },
    { id: 'history' as Page, label: '归档记录', icon: Archive },
  ] },
  { title: '工具', items: [
    { id: 'agent' as Page, label: 'Agent 助手', icon: Sparkles },
    { id: 'presets' as Page, label: '导入预设', icon: WandSparkles },
    { id: 'health' as Page, label: '恢复与健康审计', icon: ShieldCheck },
    { id: 'delivery' as Page, label: '交付包', icon: FileImage },
  ] },
]
const pageLabels: Record<Page,string> = { home:'工作台', backup:'复制新内容', transfer:'转移老内容', library:'影像资料库', history:'归档记录', task:'任务详情', agent:'Agent 助手', account:'账号与设备', presets:'导入预设', health:'恢复与健康审计', delivery:'交付包', settings:'偏好设置' }
const visibleFiles = computed(() => files.value)
const selectedTask = computed(() => taskDetail.value || tasks.value.find(task => task.id === selectedTaskId.value) || null)
const activeTitle = computed(() => pageLabels[page.value])
const globalSearchItems = computed(() => [
  ...navGroups.flatMap(group => group.items.map(item => ({ ...item, group: group.title }))),
  { id: 'account' as Page, label: '账号与设备', group: '工具', icon: LockKeyhole },
  { id: 'settings' as Page, label: '偏好设置', group: '工具', icon: Settings2 },
])
const globalSearchMatches = computed(() => {
  const q = globalSearchQuery.value.trim().toLocaleLowerCase()
  return q ? globalSearchItems.value.filter(item => item.label.toLocaleLowerCase().includes(q)) : []
})

watch(globalSearchQuery, value => {
  const exact = globalSearchItems.value.find(item => item.label === value.trim())
  if (exact) chooseGlobalSearch(exact.id)
})

function say(text: string) { notice.value = text; window.setTimeout(() => { if (notice.value === text) notice.value = '' }, 3600) }
async function loadData() {
  const [snapshot, settingData, deviceData, presetData, fileData] = await Promise.all([api.workbench.snapshot(), api.settings.get(), api.account.devices(), api.presets.list(), api.library.search('', 'all', 'all')])
  tasks.value = snapshot.tasks; summary.fileCount = snapshot.fileCount; summary.totalSize = snapshot.totalSize; summary.destinations = snapshot.destinations; summary.reviewCount = snapshot.reviewCount
  Object.assign(settings, settingData); devices.value = deviceData; presets.value = presetData; files.value = fileData
  document.documentElement.dataset.theme = settings.theme
}
onMounted(async () => { const restored = await api.auth.restore(); if (restored) authState.value = { kind: 'mock', user: restored } })
async function signIn() {
  loginError.value = ''
  if (nativeTauriRuntime && loginMode.value !== 'email-password') {
    loginError.value = '当前桌面认证切片仅接通邮箱或用户名密码登录。'
    return
  }
  if (loginMode.value === 'wechat') loginIdentifier.value ||= 'wechat-demo'
  if (!loginIdentifier.value.trim()) { loginError.value = '请填写手机号或邮箱。'; return }
  if (loginMode.value !== 'wechat' && !loginProof.value.trim()) { loginError.value = loginMode.value.endsWith('code') ? '请输入验证码。' : '请输入密码。'; return }
  loginBusy.value = true
  try {
    if (nativeTauriRuntime) {
      const identity = await passwordSignIn(loginIdentifier.value.trim(), loginProof.value)
      authState.value = { kind: 'cloudbase-provider', subject: identity.subject, expiresIn: identity.expiresIn, identifier: loginIdentifier.value.trim() }
    } else {
      authState.value = { kind: 'mock', user: await api.auth.login(loginMode.value, loginIdentifier.value, loginProof.value || 'qr-confirmed') }
    }
    await loadData(); page.value = 'home'
  }
  catch (error) { loginError.value = typeof error === 'string' ? error : error instanceof Error ? error.message : '登录失败，请稍后重试。' }
  finally { loginProof.value = ''; loginBusy.value = false }
}
async function logout() {
  if (nativeTauriRuntime) await nativeSignOut()
  else await api.auth.logout()
  authState.value = null; page.value = 'home'; loginProof.value = ''; loginIdentifier.value = ''; loginMode.value = nativeTauriRuntime ? 'email-password' : 'wechat'
}
async function scanSource() { scanBusy.value = true; sources.value = (await api.backup.scan()).sources; scanBusy.value = false; say('扫描完成：发现 1 个可读来源（演示数据）。') }
async function startBackup() { const task = await api.backup.start({ sourceId: sources.value[0]?.path || '/Volumes/SD_CARD/DCIM', destinations: backupTargets.value, presetId: backupPreset.value, verify: settings.verify }); backupTask.value = task; tasks.value = [task, ...tasks.value]; page.value = 'task'; selectedTaskId.value = task.id; taskDetail.value = task; say('演示任务已启动，不会读写本机文件。') }
async function controlTask(action: 'pause'|'resume'|'cancel'|'retry') { if (!selectedTask.value) return; taskDetail.value = await api.backup.control(selectedTask.value.id, action); tasks.value = tasks.value.map(t => t.id === taskDetail.value?.id ? taskDetail.value! : t); say(`任务状态已更新：${selectedTask.value.phase}（演示）。`) }
async function openTask(task: ArchiveTask) { selectedTaskId.value = task.id; taskDetail.value = await api.history.detail(task.id); page.value = 'task' }
async function refreshHistory() { tasks.value = await api.history.list(query.value, filterState.value) }
async function searchFiles() { files.value = await api.library.search(query.value, filterKind.value, filterQuality.value) }
async function inspectTransfer() { transferBusy.value = true; transferFiles.value = await api.transfer.inspect('旧素材来源', transferTarget.value); selectedTransfer.value = transferFiles.value.filter(f => f.kind !== 'other').map(f => f.id); transferBusy.value = false }
async function beginTransfer() { const task = await api.transfer.start(selectedTransfer.value, transferTarget.value, transferConfirmSource.value); taskDetail.value = task; tasks.value = [task, ...tasks.value]; page.value = 'task'; say(task.state === 'failed' ? '请先确认目标校验后是否移除来源。' : '转移演示已启动；来源仅在校验成功并二次确认后才会移除。') }
async function askAgent() { const text = agentInput.value.trim(); if (!text) return; agentMessages.value.push({ role:'user', text }); agentInput.value = ''; const reply = await api.agent.ask(text); agentMessages.value.push({ role:'assistant', text:reply.text, reply, actionId:reply.proposedAction ? `act-${Date.now()}` : undefined }) }
async function confirmAgent(message: { actionId?: string }, accepted: boolean) { if (!message.actionId) return; say(await api.agent.confirm(message.actionId, accepted)); message.actionId = undefined }
async function updateSetting<K extends keyof AppSettings>(key: K, value: AppSettings[K]) { Object.assign(settings, await api.settings.update({ [key]: value })); if (key === 'theme') document.documentElement.dataset.theme = settings.theme }
async function createPreset() { const name = newPresetName.value.trim() || `新建预设 ${presets.value.length + 1}`; const item = await api.presets.save({ id:`preset-${Date.now()}`, name, pattern:'日期 / 事件 / 设备 / 自定义文本', isDefault:false }); presets.value = [...presets.value, item]; newPresetName.value = ''; say('预设已加入本机演示列表。') }
async function removePreset(item: NamePreset) { if (item.isDefault) return; await api.presets.remove(item.id); presets.value = presets.value.filter(p => p.id !== item.id) }
async function runAudit() { auditResult.value = await api.audit.run() }
async function restoreFile(file: MediaFile) { say(await api.audit.restore(file.path, '桌面 / 恢复文件')) }
async function createDelivery() { const item = await api.delivery.create(deliveryName.value, deliverySelection.value, deliveryContact.value, deliveryManifest.value); say(`已创建演示交付包 ${item.id}，没有生成或移动真实文件。`) }
async function revokeDevice(device: Device) { if (device.current) return; await api.account.revoke(device.id); devices.value = await api.account.devices(); say(`${device.name} 已在演示设备列表中撤销。`) }
async function clearHistory() { await api.history.clear(); tasks.value = []; showModal.value = ''; say('演示历史已清空；真实备份文件不受影响。') }
async function renameTask(task: ArchiveTask) { const name = window.prompt('输入新的记录名称', task.name); if (!name?.trim()) return; await api.history.rename(task.id, name.trim()); task.name = name.trim(); say('记录名称已更新（演示）。') }
async function connectProvider(provider: 'baidu'|'quark') { say(await api.settings.connectProvider(provider)) }
async function resetSettings() { const value = await api.settings.reset(); Object.assign(settings,value); document.documentElement.dataset.theme = value.theme; say('设置已恢复默认演示值。') }
function setPage(next: Page) { page.value = next; taskDetail.value = next === 'task' ? selectedTask.value : null; if (next === 'history') void refreshHistory(); if (next === 'library') void searchFiles(); if (next === 'account') void api.account.devices().then(value => devices.value = value); if (next === 'presets') void api.presets.list().then(value => presets.value = value) }
function toggleGlobalSearch() { globalSearchOpen.value = !globalSearchOpen.value; globalSearchQuery.value = '' }
function chooseGlobalSearch(next: Page) { setPage(next); globalSearchOpen.value = false; globalSearchQuery.value = '' }
function submitGlobalSearch() { const match = globalSearchMatches.value[0]; if (match) chooseGlobalSearch(match.id) }
function taskStateLabel(state: TaskState) { return ({ queued:'排队中', running:'进行中', paused:'已暂停', success:'已完成', partial:'部分完成', cancelled:'已取消', failed:'失败' } as Record<TaskState,string>)[state] }
</script>

<template>
  <main v-if="!authState" class="login-shell">
    <div class="login-brand"><div class="brand-mark"><i></i><i></i><i></i><i></i></div><span>影序 <small>YINGXU</small></span></div>
    <section class="login-card">
      <div class="login-intro"><span class="eyebrow">影序 · 桌面端 V2 草稿</span><h1>欢迎回到影序</h1><p>{{ nativeTauriRuntime ? '验证 CloudBase 账号身份。当前桌面工作区仍为本地演示数据，影序会话交换与跨设备同步尚未接入。' : '登录后同步备份进度、归档记录与设备状态。你的照片和视频原片不会上传到影序云端。' }}</p></div>
      <div class="login-tabs"><button v-if="!nativeTauriRuntime" :class="{chosen:loginMode==='wechat'}" @click="loginMode='wechat'">微信扫码</button><button v-if="!nativeTauriRuntime" :class="{chosen:loginMode==='phone-code'}" @click="loginMode='phone-code'">手机号</button><button :class="{chosen:loginMode==='email-code'||loginMode==='email-password'}" @click="loginMode=nativeTauriRuntime?'email-password':'email-code'">{{ nativeTauriRuntime?'邮箱 / 用户名':'邮箱' }}</button></div>
      <div v-if="loginMode==='wechat'" class="wechat-login"><div class="qr-frame"><div class="qr-code"><i v-for="n in 121" :key="n" :class="{quiet:(n*7+n%5)%4===0 || (n%11===0 && n>20)}"></i><b>影</b></div></div><strong>使用微信扫描二维码登录</strong><span>请使用与影序 Pocket 相同的账号</span><button class="text-button" @click="loginMode='phone-code'">改用验证码登录</button></div>
      <div v-else class="login-fields">
        <label>{{ loginMode==='phone-code'?'手机号':nativeTauriRuntime?'邮箱或用户名':'邮箱' }}</label><input v-model="loginIdentifier" :placeholder="loginMode==='phone-code'?'请输入手机号':nativeTauriRuntime?'请输入邮箱地址或用户名':'请输入邮箱地址'" />
        <label>{{ loginMode==='email-password'?'密码':'验证码' }}</label><div class="proof-field"><input v-model="loginProof" :type="loginMode==='email-password'?'password':'text'" :placeholder="loginMode==='email-password'?'请输入密码':'请输入验证码'" /><button v-if="loginMode!=='email-password'" class="text-button" @click="say('验证码发送流程将在后端认证服务接入后启用。')">获取验证码</button></div>
        <div v-if="loginMode==='email-code'" class="form-foot"><button class="text-button" @click="loginMode='email-password'">使用 Pocket 邮箱密码</button><button class="text-button" @click="loginMode='email-password'">注册账号</button></div>
      </div>
      <p v-if="loginError" class="error-note">{{ loginError }}</p>
      <button class="primary-button login-submit" @click="signIn" :disabled="loginBusy">{{ loginBusy?'正在登录…':nativeTauriRuntime?'验证 CloudBase 账号':'登录影序（演示）' }}</button>
      <p class="login-disclaimer">{{ nativeTauriRuntime?'通过本机 Tauri 安全通道验证 CloudBase 身份；这不代表影序云端会话或同步已建立。验证码登录将在后续版本接入':'V2 草稿预览 · 登录仅为界面演示，不会连接认证服务' }}</p>
    </section>
    <footer class="login-footer"><span><LockKeyhole :size="14" /> 安全同步 · 原片留在你的设备</span><span>Windows / macOS</span></footer>
    <div v-if="notice" class="toast">{{ notice }}</div>
  </main>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-row"><div class="brand-mark"><i></i><i></i><i></i><i></i></div><div><b>影序 <small>YINGXU</small></b><span>智能影像管理</span></div></div>
      <button class="workspace-switch"><span class="workspace-avatar">{{ activeDisplayName.slice(0,1) }}</span><span><b>{{ activeDisplayName }} 的空间</b><small>本机设备 · Mac Studio</small></span><ChevronDown :size="14" /></button>
      <nav class="side-nav"><section v-for="group in navGroups" :key="group.title"><h2>{{ group.title }}</h2><button v-for="item in group.items" :key="item.id" :class="['nav-item',{active:page===item.id}]" :aria-label="item.label" :title="item.label" @click="setPage(item.id)"><component :is="item.icon" :size="17"/><span>{{ item.label }}</span><i v-if="item.id==='backup' && tasks.some(t=>t.state==='running')" class="nav-dot"></i></button></section></nav>
      <div class="side-bottom"><button class="nav-item" :class="{active:page==='account'}" aria-label="账号与设备" title="账号与设备" @click="setPage('account')"><LockKeyhole :size="17"/><span>账号与设备</span></button><button class="nav-item" :class="{active:page==='settings'}" aria-label="偏好设置" title="偏好设置" @click="setPage('settings')"><Settings2 :size="17"/><span>偏好设置</span></button><div class="account-chip"><span class="user-avatar">{{ activeDisplayName.slice(0,1) }}</span><span><b>{{ activeDisplayName }}</b><small>{{ activeEmail }}</small></span><button class="icon-button" title="退出登录" @click="logout"><MoreHorizontal :size="16"/></button></div></div>
    </aside>
    <section class="main-area">
      <header class="topbar"><div class="breadcrumbs"><span>影序</span><ChevronRight :size="13"/><b>{{ activeTitle }}</b></div><div class="top-actions"><span class="draft-chip"><i></i> V2 草稿预览</span><span class="demo-chip"><i></i> 演示数据</span><div class="search-action-wrap"><button class="icon-button" aria-label="搜索工作区和工具" :aria-expanded="globalSearchOpen" @click="toggleGlobalSearch"><Search :size="17"/></button><span v-if="!globalSearchOpen" class="search-tooltip" role="tooltip">搜索</span><div v-if="globalSearchOpen" class="global-search-popover" @keydown.esc="globalSearchOpen=false"><form class="global-search-field" @submit.prevent="submitGlobalSearch"><Search :size="16"/><input v-model="globalSearchQuery" autofocus placeholder="搜索工作区和工具" aria-label="搜索工作区和工具"/><button v-if="globalSearchQuery" type="button" class="global-search-clear" aria-label="清除搜索" @click="globalSearchQuery=''">×</button></form><div v-if="globalSearchQuery.trim() && globalSearchMatches.length" class="global-search-results"><button v-for="item in globalSearchMatches" :key="item.id" class="global-search-result" @click="chooseGlobalSearch(item.id)"><component :is="item.icon" :size="15"/><span>{{ item.label }}</span><small>{{ item.group }}</small></button></div><div v-else-if="globalSearchQuery.trim()" class="global-search-empty">未搜索到相关页面</div><div v-else class="global-search-hint">搜索工作区与工具，输入完整名称可直接跳转</div></div></div><button class="icon-button" aria-label="通知" title="通知" @click="say('当前没有新的演示通知。')"><Bell :size="17"/></button><span class="top-divider"></span><button class="theme-button" @click="updateSetting('theme',settings.theme==='light'?'dark':'light')"><component :is="settings.theme==='light'?Sun:Moon" :size="15"/>{{ settings.theme==='light'?'浅色':'深色' }}</button></div></header>
      <div class="page-scroll"><div class="page-content">
        <div v-if="notice" class="toast inline-toast">{{ notice }}</div>
        <div v-if="authState?.kind==='cloudbase-provider'" class="notice-card cloudbase-boundary-notice"><ShieldCheck :size="18"/><div><b>CloudBase 身份已验证</b><span>影序短会话交换和云端同步尚未接入。此处展示的工作区、任务、素材与设备数据均为本地 Mock 演示数据。</span></div></div>

        <template v-if="page==='home'">
          <header class="welcome-row"><div><span class="eyebrow">桌面工作空间 · 演示模式</span><h1>早上好，{{ activeDisplayName }} <span>☀</span></h1><p>今天也为你的每一份作品，留好安全副本。</p></div><button class="primary-button" @click="backupStep=1;setPage('backup')"><Plus :size="16"/> 新建备份任务</button></header>
          <section class="hero-card"><div><span class="hero-eyebrow"><i class="status-dot running"></i> {{ tasks[0]?.phase || '任务状态' }} · {{ tasks[0]?.progress || 0 }}%</span><h2>{{ tasks[0]?.name || '欢迎开始你的第一次归档' }}</h2><p>原片保留在来源设备；任务进度、校验状态和目标结果可随时查看。</p><div class="progress-line"><i :style="{width:`${tasks[0]?.progress||0}%`}"></i></div><div class="hero-meta"><span>{{ tasks[0]?.source || '尚无来源' }}</span><b>{{ tasks[0]?.speed || '等待任务' }}</b></div></div><span class="hero-art"><Image :size="42"/><ShieldCheck :size="22"/></span><button class="hero-link" @click="tasks[0]&&openTask(tasks[0])"><ArrowRight :size="17"/></button></section>
          <section class="stat-grid"><article class="stat-card"><span class="stat-icon green"><Database :size="17"/></span><small>已归档文件</small><b>{{ summary.fileCount.toLocaleString() }} <em>个</em></b><span>{{ summary.totalSize }} 安全副本</span></article><article class="stat-card"><span class="stat-icon gold"><Activity :size="17"/></span><small>近 30 天任务</small><b>{{ tasks.length }} <em>个</em></b><span>包含部分完成与失败记录</span></article><article class="stat-card"><span class="stat-icon blue"><Cloud :size="17"/></span><small>同步目的地</small><b>{{ summary.destinations }} <em>个</em></b><span>网盘状态为演示数据</span></article><article class="stat-card"><span class="stat-icon violet"><Sparkles :size="17"/></span><small>质量待复核</small><b>{{ summary.reviewCount }} <em>项</em></b><span>不会自动删除原片</span></article></section>
          <section class="content-grid"><div class="panel"><div class="panel-heading"><div><h3>最近归档</h3><p>查看任务状态与逐个目的地结果</p></div><button class="text-button" @click="setPage('history')">全部记录 <ArrowRight :size="14"/></button></div><div class="task-list"><button v-for="task in tasks.slice(0,4)" :key="task.id" class="task-row" @click="openTask(task)"><span class="task-icon"><Archive :size="17"/></span><span class="task-name"><b>{{ task.name }}</b><small>{{ task.createdAt }} · {{ task.files }} 个 · {{ task.size }}</small></span><span class="task-dests"><i v-for="dest in task.destinations" :key="dest.id" :class="['mini-state',dest.state]">{{ dest.name }} · {{ taskStateLabel(dest.state) }}</i></span><span :class="['state-pill',task.state]">{{ taskStateLabel(task.state) }}</span><ChevronRight :size="15"/></button></div></div><div class="panel quick-panel"><div class="panel-heading"><div><h3>继续处理</h3><p>常用影像工作流</p></div><WandSparkles :size="17"/></div><button class="quick-action" @click="setPage('transfer')"><Folder :size="17"/><span><b>转移老内容</b><small>校验后确认移除来源</small></span><ArrowRight :size="14"/></button><button class="quick-action" @click="setPage('health')"><ShieldCheck :size="17"/><span><b>备份健康审计</b><small>检查副本完整性</small></span><ArrowRight :size="14"/></button><button class="quick-action" @click="setPage('agent')"><Sparkles :size="17"/><span><b>询问 Agent</b><small>检索素材与任务</small></span><ArrowRight :size="14"/></button></div></section>
          <section class="panel notice-card"><ShieldCheck :size="18"/><div><b>安全提醒</b><span>影序演示不会读写本机文件。真实备份、云盘授权与账号同步将在前端确认后分阶段接入。</span></div><button class="text-button" @click="setPage('settings')">查看隐私设置 <ArrowRight :size="13"/></button></section>
        </template>

        <template v-else-if="page==='backup'">
          <PageHeading eyebrow="安全导入" title="复制新内容" description="检测来源、预检目的地并预览命名；只有你确认后才开始备份。"/>
          <div class="stepper"><button v-for="(label,index) in ['来源扫描','备份目的地','命名预览','确认摘要']" :key="label" :class="{current:backupStep===index+1,done:backupStep>index+1}" @click="backupStep=index+1"><i>{{ backupStep>index+1?'✓':index+1 }}</i>{{ label }}</button></div>
          <section class="panel flow-panel">
            <template v-if="backupStep===1"><div class="panel-heading"><div><h3>选择来源设备</h3><p>来源以只读方式扫描，不自动开始写入。</p></div><button class="secondary-button" @click="scanSource" :disabled="scanBusy">{{ scanBusy?'扫描中…':'重新扫描来源' }}</button></div><div v-if="sources.length" class="source-card"><HardDrive :size="22"/><div><b>{{ sources[0].name }} · 已连接</b><span>{{ sources[0].path }} · {{ sources[0].files.toLocaleString() }} 个文件 · {{ sources[0].size }}</span></div><CheckCircle2 :size="18"/></div><div v-else class="empty-state"><HardDrive :size="28"/><b>尚未扫描来源</b><span>插入存储卡或移动盘后手动开始预检。</span><button class="primary-button" @click="scanSource" :disabled="scanBusy">{{ scanBusy?'扫描中…':'开始扫描（演示）' }}</button></div><div class="scan-note"><ShieldCheck :size="16"/>扫描仅读取文件信息；未连接真实磁盘。</div></template>
            <template v-else-if="backupStep===2"><div class="panel-heading"><div><h3>选择备份目的地</h3><p>支持本地磁盘、百度网盘与夸克网盘；不支持 NAS/局域网目录。</p></div></div><div class="option-list"><label v-for="item in [{id:'local',name:'本机硬盘',meta:'Mac Studio / 摄影归档 · 可用 616 GB'},{id:'baidu',name:'百度网盘',meta:'需要官方账号授权'},{id:'quark',name:'夸克网盘',meta:'官方 API 能力待核验'}]" :key="item.id" class="option-row"><input v-model="backupTargets" type="checkbox" :value="item.id" :disabled="item.id!=='local'"/><span><b>{{ item.name }}</b><small>{{ item.meta }}</small></span><span class="demo-label">{{ item.id==='local'?'可用':'待接入' }}</span></label></div><div class="scan-note">容量预检、哈希冲突处理和多目的地状态按框架定义为独立 API；当前由 mock adapter 提供演示结果。</div></template>
            <template v-else-if="backupStep===3"><div class="panel-heading"><div><h3>命名规则与目录预览</h3><p>可应用导入预设，并在执行前检查层级名称。</p></div><button class="text-button" @click="setPage('presets')">管理预设 <ArrowRight :size="13"/></button></div><label class="field-label">导入预设</label><select v-model="backupPreset" class="select-field"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.name }} · {{ preset.pattern }}</option></select><div class="rule-chips"><span>拍摄日期</span><ChevronRight :size="13"/><span>事件名称</span><ChevronRight :size="13"/><span>设备型号</span><button class="secondary-button compact" @click="say('规则编辑表单已在 V2 预览中留位，复杂规则由本机任务接口生成。')"><Plus :size="13"/>编辑规则</button></div><div class="path-preview"><Folder :size="15"/>摄影归档 / 2025 / 04-21_海边日落 / Sony_A7_IV</div><label class="check-row"><input v-model="settings.verify" type="checkbox"/>完成复制后执行 SHA-256 完整性校验</label></template>
            <template v-else><div class="panel-heading"><div><h3>确认备份任务</h3><p>确认后才会启动演示任务；本页不会操作真实文件。</p></div><span class="state-pill queued">等待确认</span></div><dl class="summary-list"><div><dt>来源</dt><dd>{{ sources[0]?.path || 'SD_CARD/DCIM（示例）' }}</dd></div><div><dt>备份目标</dt><dd>{{ backupTargets.map(id=>id==='local'?'本机硬盘':id==='baidu'?'百度网盘':'夸克网盘').join('、') || '请选择目标' }}</dd></div><div><dt>命名预设</dt><dd>{{ presets.find(p=>p.id===backupPreset)?.name || '默认规则' }}</dd></div><div><dt>文件与校验</dt><dd>1,248 个 · 42.8 GB · {{ settings.verify?'SHA-256':'按设置跳过校验' }}</dd></div></dl><div class="consent-box"><ShieldCheck :size="18"/><span>普通备份只复制；同名不覆盖。网盘上传必须等本地副本校验后执行。</span></div></template>
            <div class="panel-actions"><button class="secondary-button" v-if="backupStep>1" @click="backupStep--">上一步</button><span v-else></span><button v-if="backupStep<4" class="primary-button" @click="backupStep===1&&!sources.length?scanSource():backupStep++">{{ backupStep===1&&!sources.length?'先扫描来源':'继续' }} <ArrowRight :size="15"/></button><button v-else class="primary-button" @click="startBackup" :disabled="!backupTargets.length"><Play :size="15"/>确认并开始（演示）</button></div>
          </section>
        </template>

        <template v-else-if="page==='transfer'">
          <PageHeading eyebrow="安全迁移" title="转移老内容" description="来源先只读扫描；目标副本通过校验并经你再次确认后，才会移除来源。"/>
          <section class="panel flow-panel"><div class="two-column-fields"><label><span>来源目录</span><input value="/Volumes/旧硬盘/待整理" readonly/></label><label><span>目标目录</span><input v-model="transferTarget"/></label></div><div class="panel-heading"><div><h3>转移候选</h3><p>可按照片、视频、其他文件筛选；疑似废片只做标记。</p></div><button class="secondary-button" @click="inspectTransfer" :disabled="transferBusy">{{ transferBusy?'只读扫描中…':'扫描并分析（演示）' }}</button></div><div v-if="transferFiles.length" class="file-table"><div class="table-head"><span>选择</span><span>文件</span><span>类型 / 大小</span><span>质量与重复提示</span><span>来源处理</span></div><label v-for="file in transferFiles" :key="file.id" class="table-row"><input v-model="selectedTransfer" type="checkbox" :value="file.id"/><span><b>{{ file.name }}</b><small>{{ file.path }}</small></span><span>{{ file.kind }} · {{ file.size }}</span><span>{{ file.quality }}</span><span>校验后待确认</span></label></div><div v-else class="empty-state compact-empty"><Folder :size="25"/><b>先扫描来源</b><span>扫描不会移动或删除文件。</span></div><label class="check-row danger-check"><input v-model="transferConfirmSource" type="checkbox"/>我确认：仅在目标副本 SHA-256 校验成功且来源未变化后，移除已转移来源文件</label><div class="panel-actions"><span class="demo-label">候选删除只进入待确认目录，不会自动清理</span><button class="primary-button" @click="beginTransfer" :disabled="!selectedTransfer.length"><ArrowRight :size="15"/>预览并启动转移（演示）</button></div></section>
        </template>

        <template v-else-if="page==='library'">
          <PageHeading eyebrow="本机素材 · 演示索引" title="影像资料库" description="按名称、日期、设备、地点、类型和质量标签检索素材。"/>
          <section class="panel"><div class="filter-toolbar"><div class="search-field"><Search :size="16"/><input v-model="query" placeholder="搜索文件名、设备、地点或路径" @keydown.enter="searchFiles"/><button class="text-button" @click="searchFiles">搜索</button></div><select v-model="filterKind" class="select-field small-select" @change="searchFiles"><option value="all">全部类型</option><option value="photo">照片</option><option value="video">视频</option><option value="other">关联文件</option></select><select v-model="filterQuality" class="select-field small-select" @change="searchFiles"><option value="all">全部质量</option><option value="good">正常</option><option value="review">待复核</option><option value="unanalyzed">未分析</option></select></div><div class="media-grid"><article v-for="file in visibleFiles" :key="file.id" class="media-card"><div :class="['media-art',file.kind,file.qualityState]"><Image v-if="file.kind==='photo'" :size="25"/><FileVideo v-else-if="file.kind==='video'" :size="25"/><Copy v-else :size="25"/><span>{{ file.kind==='photo'?'PHOTO':file.kind==='video'?'VIDEO':'SIDECAR' }}</span></div><div class="media-copy"><b>{{ file.name }}</b><small>{{ file.capturedAt }} · {{ file.device }}</small><small>{{ file.location }} · {{ file.size }}</small><div class="quality-row"><span :class="file.qualityState">{{ file.quality }}</span><button class="text-button" @click="restoreFile(file)">恢复副本</button></div></div></article><div v-if="!files.length" class="empty-state">没有匹配素材，请调整搜索或筛选条件。</div></div><p class="table-foot">资料库索引与视觉检索使用本机 mock 数据；未读取真实文件。</p></section>
        </template>

        <template v-else-if="page==='history'">
          <PageHeading eyebrow="可追溯记录" title="归档记录" description="查看任务状态、逐个目标结果、失败原因和恢复步骤。"/>
          <section class="panel"><div class="filter-toolbar"><div class="search-field"><Search :size="16"/><input v-model="query" placeholder="搜索任务名称或任务 ID" @keydown.enter="refreshHistory"/><button class="text-button" @click="refreshHistory">搜索</button></div><select v-model="filterState" class="select-field small-select"><option value="all">全部状态</option><option value="success">成功</option><option value="partial">部分完成</option><option value="running">进行中</option><option value="paused">暂停</option><option value="cancelled">已取消</option><option value="failed">失败</option></select><button class="secondary-button" @click="showModal='clear-history'">清空历史</button></div><div class="history-list"><article v-for="task in tasks" :key="task.id" class="history-row"><div class="task-icon"><Archive :size="17"/></div><div class="task-name"><b>{{ task.name }}</b><small>{{ task.id }} · {{ task.createdAt }} · {{ task.files }} 个 · {{ task.size }}</small></div><div class="task-dests"><i v-for="destination in task.destinations" :key="destination.id" :class="['mini-state',destination.state]">{{ destination.name }} · {{ taskStateLabel(destination.state) }}{{ destination.error?`：${destination.error}`:'' }}</i></div><span :class="['state-pill',task.state]">{{ taskStateLabel(task.state) }}</span><button class="text-button" @click="renameTask(task)">改名</button><button class="secondary-button compact" @click="openTask(task)">详情 / 报告</button></article><div v-if="!tasks.length" class="empty-state">没有符合条件的归档记录。</div></div><p class="table-foot">清空记录会二次确认，不会删除媒体文件。</p></section>
        </template>

        <template v-else-if="page==='task'">
          <PageHeading :eyebrow="`任务 ID · ${selectedTask?.id || '—'}`" title="任务详情与报告" description="本机任务阶段、文件级结果和逐目的地状态。演示数据不会映射到真实文件。"><button class="secondary-button" @click="setPage('history')"><Archive :size="15"/>返回归档记录</button></PageHeading>
          <section v-if="selectedTask" class="task-detail-layout"><div class="panel"><div class="panel-heading"><div><h3>{{ selectedTask.name }}</h3><p>{{ selectedTask.createdAt }} · 来源：{{ selectedTask.source }}</p></div><span :class="['state-pill',selectedTask.state]">{{ taskStateLabel(selectedTask.state) }}</span></div><div class="detail-progress"><div class="progress-line"><i :style="{width:`${selectedTask.progress}%`}"></i></div><b>{{ selectedTask.progress }}%</b><span>{{ selectedTask.phase }}</span></div><div class="detail-stats"><span><b>{{ selectedTask.files }}</b> 已处理 / 计划文件</span><span><b>{{ selectedTask.size }}</b> 数据量</span><span><b>{{ selectedTask.speed }}</b> 当前速度</span></div><div class="destination-list"><h4>目的地结果</h4><div v-for="destination in selectedTask.destinations" :key="destination.id" class="destination-row"><Cloud :size="17"/><span><b>{{ destination.name }}</b><small>{{ destination.error || `阶段：${taskStateLabel(destination.state)} · ${destination.progress}%` }}</small></span><span :class="['state-pill',destination.state]">{{ taskStateLabel(destination.state) }}</span><button v-if="destination.state==='failed'" class="secondary-button compact" @click="api.history.retry(selectedTask.id,destination.id).then(task=>{taskDetail=task;say('失败目标正在演示重试。')})">重试此目标</button></div></div><div class="panel-actions"><button v-if="selectedTask.state==='running'" class="secondary-button" @click="controlTask('pause')">暂停</button><button v-if="selectedTask.state==='paused'" class="secondary-button" @click="controlTask('resume')">继续</button><button v-if="['running','paused'].includes(selectedTask.state)" class="danger-button" @click="controlTask('cancel')">取消剩余任务</button><button class="secondary-button" @click="say('报告预览包含任务ID、来源目标、校验与失败摘要；当前仅演示。')">查看报告</button></div></div><aside class="panel report-panel"><h3>报告摘要</h3><p>任务 ID、版本、来源/目标、文件数、容量、速度、校验、跳过和失败原因。</p><dl class="summary-list"><div><dt>版本</dt><dd>V2 草稿预览</dd></div><div><dt>完整性</dt><dd>{{ settings.verify?'SHA-256 校验':'校验已跳过' }}</dd></div><div><dt>来源安全</dt><dd>普通备份保留来源</dd></div><div><dt>同步状态</dt><dd>离线演示，尚未同步</dd></div></dl></aside></section>
        </template>

        <template v-else-if="page==='agent'">
          <PageHeading eyebrow="受限工具 · 操作需确认" title="Agent 助手" description="查找和预览可直接进行；改名、移动、上传、清理等操作先列出对象与目标。"/>
          <section class="agent-layout"><aside class="panel agent-aside"><button class="primary-button full-width" @click="agentMessages=[]"><Plus :size="15"/>新建对话</button><h4>最近对话</h4><button class="conversation-link"><Sparkles :size="14"/>查找海边日落照片 <small>今天 10:12</small></button><button class="conversation-link"><Archive :size="14"/>昨晚的备份任务结果 <small>昨天 19:34</small></button><div class="safety-callout"><ShieldCheck :size="16"/>所有有副作用的操作都需你确认。</div></aside><div class="panel chat-panel"><div class="chat-scroll"><div class="agent-welcome"><span class="agent-orb"><Sparkles :size="20"/></span><b>你好，{{ activeDisplayName }}</b><p>可以查找素材、询问备份状态，或先预览文件操作计划。</p><div class="suggestion-tags"><button @click="agentInput='查找青岛海边照片'">查找青岛海边照片</button><button @click="agentInput='查看最近失败的任务'">查看最近失败的任务</button></div></div><div v-for="(message,index) in agentMessages" :key="index" :class="['message-row',message.role]"><b v-if="message.role==='user'">{{ activeDisplayName }}</b><span v-else class="agent-mini">✦</span><div class="message-content"><p>{{ message.text }}</p><div v-if="message.reply?.matches.length" class="match-list"><article v-for="file in message.reply.matches" :key="file.id"><Image :size="15"/><span><b>{{ file.name }}</b><small>{{ file.path }} · {{ file.quality }}</small></span></article></div><div v-if="message.reply?.proposedAction" class="action-plan"><b>待确认操作计划</b><span>操作：{{ message.reply.proposedAction.kind }} · {{ message.reply.proposedAction.label }}</span><span>对象：{{ message.reply.proposedAction.objects.join('、') }}</span><span>目标：{{ message.reply.proposedAction.target }}</span><div v-if="message.actionId" class="plan-actions"><button class="secondary-button compact" @click="confirmAgent(message,false)">取消</button><button class="primary-button compact" @click="confirmAgent(message,true)">确认演示操作</button></div></div></div></div></div><form class="composer" @submit.prevent="askAgent"><input v-model="agentInput" placeholder="例如：把今天的 RAW 文件整理到项目目录…"/><button class="primary-button" type="submit"><Send :size="15"/>发送</button></form><p class="table-foot centered">演示 Agent 不会读取或修改本机文件；真实工具采用白名单与逐次确认。</p></div></section>
        </template>

        <template v-else-if="page==='account'">
          <PageHeading eyebrow="个人账号与设备" title="账号与设备" :description="nativeTauriRuntime ? '当前仅验证 CloudBase 身份；影序账户会话与设备同步尚未接入。' : '桌面端与 Pocket 共用影序账号；这里管理登录方式、设备会话和可选同步字段。'"/>
          <section class="account-grid"><div class="panel account-card"><div class="account-profile"><span class="profile-avatar">{{ activeDisplayName.slice(0,1) }}</span><div><h3>{{ activeDisplayName }}</h3><p>{{ authState?.kind==='cloudbase-provider' ? 'CloudBase 身份已验证 · 影序会话未交换' : `${activeEmail} · 已登录演示账号` }}</p></div><span class="state-pill success">本机在线</span></div><h4>登录方式关联</h4><div class="provider-row"><span class="method-icon wechat">微</span><span><b>微信扫码</b><small>账号关联将在认证后端接入时启用</small></span><button class="secondary-button compact" disabled>待接入</button></div><div class="provider-row"><span class="method-icon">@</span><span><b>{{ authState?.kind==='cloudbase-provider' ? 'CloudBase 账号' : '邮箱账号' }}</b><small>{{ activeEmail || activeDisplayName }} · 当前身份提供方</small></span><button class="text-button" disabled>{{ authState?.kind==='cloudbase-provider' ? '已验证' : '演示账号' }}</button></div><button class="danger-button" @click="logout">退出当前账号</button></div><div class="panel sync-card"><div class="panel-heading"><div><h3>跨设备同步范围</h3><p>原片、令牌和精确 GPS/EXIF 不同步；当前本页内容为演示数据。</p></div></div><label class="setting-row"><span><b>备份记录</b><small>同步任务状态和目的地摘要</small></span><input type="checkbox" :checked="settings.syncHistory" @change="updateSetting('syncHistory',($event.target as HTMLInputElement).checked)"/></label><label class="setting-row"><span><b>Agent 会话</b><small>用户/助手文本、工具摘要和结果</small></span><input type="checkbox" :checked="settings.syncAgent" @change="updateSetting('syncAgent',($event.target as HTMLInputElement).checked)"/></label><label class="setting-row"><span><b>压缩缩略图</b><small>最长边约 512px，可随时关闭</small></span><input type="checkbox" :checked="settings.syncThumbnails" @change="updateSetting('syncThumbnails',($event.target as HTMLInputElement).checked)"/></label><label class="setting-row"><span><b>电脑名称与完整路径</b><small>手机仅可查看路径，不可打开或控制电脑</small></span><input type="checkbox" :checked="settings.syncPaths" @change="updateSetting('syncPaths',($event.target as HTMLInputElement).checked)"/></label><p class="table-foot">数据删除与注销级联清除能力将在真实云端阶段实现。</p></div></section>
          <section class="panel devices-panel"><div class="panel-heading"><div><h3>设备与会话</h3><p>在线状态和最后同步时间（演示）</p></div><button class="text-button" @click="say('设备列表已刷新。')">刷新</button></div><article v-for="device in devices" :key="device.id" class="device-row"><span class="device-icon"><component :is="device.platform==='Android'||device.platform==='iOS'?Image:HardDrive" :size="17"/></span><span><b>{{ device.name }} <small v-if="device.current">当前设备</small></b><small>{{ device.platform }} · {{ device.lastSeen }}</small></span><span :class="['state-pill',device.online?'success':'queued']">{{ device.online?'在线':'离线' }}</span><button v-if="!device.current" class="secondary-button compact" @click="revokeDevice(device)">撤销设备</button></article></section>
        </template>

        <template v-else-if="page==='presets'">
          <PageHeading eyebrow="重复使用的导入策略" title="导入预设" description="保存命名规则、目的地组合和质量筛选策略；每次任务仍需预览确认。"><button class="primary-button" @click="createPreset"><Plus :size="15"/>新建预设</button></PageHeading>
          <section class="preset-grid"><article v-for="preset in presets" :key="preset.id" class="panel preset-card"><span class="preset-icon"><WandSparkles :size="20"/></span><span v-if="preset.isDefault" class="state-pill success">默认预设</span><h3>{{ preset.name }}</h3><p>{{ preset.pattern }}</p><div class="preset-meta"><span>本机保存 · 演示规则</span><div><button class="text-button" @click="backupPreset=preset.id;setPage('backup');backupStep=3">应用到备份</button><button v-if="!preset.isDefault" class="text-button danger-text" @click="removePreset(preset)">删除</button></div></div></article><article class="panel preset-create"><label class="field-label">快速添加预设名称</label><input v-model="newPresetName" placeholder="例如：旅行项目归档"/><button class="secondary-button" @click="createPreset"><Plus :size="14"/>添加</button></article></section>
          <section class="panel notice-card"><ShieldCheck :size="17"/><div><b>应用前检查</b><span>目标、命名层级和筛选规则会显示在备份摘要；预设不会绕过确认步骤。</span></div></section>
        </template>

        <template v-else-if="page==='health'">
          <PageHeading eyebrow="可恢复的数据检查" title="恢复与健康审计" description="检查已备份副本可用性，并从通过校验的副本恢复到新位置。"><button class="primary-button" @click="runAudit"><Activity :size="15"/>运行健康审计（演示）</button></PageHeading>
          <section class="panel"><div v-if="auditResult" class="audit-summary"><div><b>{{ auditResult.checked }}</b><span>已抽查文件</span></div><div><b>{{ auditResult.healthy }}</b><span>校验正常</span></div><div><b>{{ auditResult.issues.length }}</b><span>需处理项目</span></div><span class="demo-label">{{ auditResult.lastRun }}</span></div><div v-else class="empty-state"><ShieldCheck :size="28"/><b>尚未运行审计</b><span>审计只检查备份副本，不会修改来源或目标文件。</span><button class="secondary-button" @click="runAudit">开始抽查（演示）</button></div><div v-if="auditResult" class="issue-list"><article v-for="issue in auditResult.issues" :key="issue.path" class="issue-row"><span class="status-dot running"></span><span><b>{{ issue.path }}</b><small>{{ issue.reason }} · {{ issue.recoverable?'可从其他健康副本恢复':'需要人工检查' }}</small></span><button class="secondary-button compact" @click="say(`恢复预览：${issue.path} → 指定目标。未执行文件写入。`)">预览恢复</button></article></div></section><section class="panel restore-panel"><h3>恢复文件</h3><p>选择健康副本与另存位置；默认不覆盖已有文件。</p><div class="two-column-fields"><label><span>恢复来源</span><select class="select-field"><option>已校验备份副本 · 山野纪行</option></select></label><label><span>恢复目标</span><input value="桌面 / 恢复文件（演示）"/></label></div><button class="primary-button" @click="say('恢复流程将重新计算 SHA-256 并提供同名文件另存选项；当前仅展示。')">选择文件并预览恢复</button></section>
        </template>

        <template v-else-if="page==='delivery'">
          <PageHeading eyebrow="原片保持不变" title="客户交付包" description="将选中素材副本写入独立目录，可附联系表、清单与 SHA-256 校验文件。"/>
          <section class="delivery-layout"><div class="panel"><div class="panel-heading"><div><h3>选择交付素材</h3><p>照片、视频与已批准的副本（演示样本）</p></div><span class="demo-label">{{ deliverySelection.length }} 项已选</span></div><label v-for="file in files.filter(f=>f.kind!=='other')" :key="file.id" class="delivery-file"><input v-model="deliverySelection" type="checkbox" :value="file.id"/><span class="delivery-file-main"><b>{{ file.name }}</b><small>{{ file.capturedAt }} · {{ file.size }}</small></span><span class="delivery-file-quality">{{ file.quality }}</span></label></div><div class="panel"><h3>交付设置</h3><label class="field-label">交付包名称</label><input v-model="deliveryName"/><label class="check-row"><input v-model="deliveryContact" type="checkbox"/>生成联系表 / 预览图</label><label class="check-row"><input v-model="deliveryManifest" type="checkbox"/>包含文件清单与 SHA-256 校验文件</label><div class="consent-box"><ShieldCheck :size="16"/><span>交付包写入独立目录；不会移动或改写原始素材。</span></div><button class="primary-button full-width" @click="createDelivery" :disabled="!deliverySelection.length"><PackageGlyph/>生成交付包（演示）</button></div></section>
          <section class="panel"><div class="panel-heading"><div><h3>最近交付包</h3><p>生成记录（仅演示）</p></div></div><p class="table-foot">创建交付包后显示生成摘要、文件数和清单状态。</p></section>
        </template>

        <template v-else>
          <PageHeading eyebrow="桌面端配置" title="偏好设置" description="配置主题、备份、智能整理、云盘、通知、同步和隐私偏好。"><button class="secondary-button" @click="resetSettings">恢复默认值</button></PageHeading>
          <div class="settings-layout"><nav class="panel settings-nav"><a href="#general">通用</a><a href="#backup-settings">备份与校验</a><a href="#naming">命名与 AI</a><a href="#quality">质量分析</a><a href="#cloud">云盘与同步</a><a href="#advanced">通知与高级</a><a href="#privacy">隐私安全</a></nav><div class="settings-content">
            <section id="general" class="panel settings-section"><div class="panel-heading"><div><h3>通用与外观</h3><p>跨 Windows/macOS 的桌面偏好</p></div></div><label class="setting-row"><span><b>外观主题</b><small>保持移动端语义色</small></span><select class="select-field small-select" :value="settings.theme" @change="updateSetting('theme',($event.target as HTMLSelectElement).value as AppSettings['theme'])"><option value="light">浅色</option><option value="dark">深色</option></select></label><label class="setting-row"><span><b>登录桌面后启动</b><small>应用开机启动由桌面宿主接入</small></span><input type="checkbox" :checked="settings.startOnLogin" @change="updateSetting('startOnLogin',($event.target as HTMLInputElement).checked)"/></label><label class="setting-row"><span><b>关闭窗口后后台运行</b><small>托盘/菜单栏生命周期由 Tauri 宿主接入</small></span><input type="checkbox" :checked="settings.backgroundOnClose" @change="updateSetting('backgroundOnClose',($event.target as HTMLInputElement).checked)"/></label></section>
            <section id="backup-settings" class="panel settings-section"><div class="panel-heading"><div><h3>备份、校验与冲突</h3><p>安全默认值与 SHA-256 行为</p></div></div><label class="setting-row"><span><b>复制后校验</b><small>可在任务摘要逐次调整</small></span><input type="checkbox" :checked="settings.verify" @change="updateSetting('verify',($event.target as HTMLInputElement).checked)"/></label><div class="setting-row"><span><b>同名文件策略</b><small>内容相同跳过；不同内容添加序号</small></span><span class="state-pill success">不覆盖原文件</span></div><div class="setting-row"><span><b>本机空间与已备份统计</b><small>显示 384 GB / 1 TB · 演示值</small></span><HardDrive :size="16"/></div></section>
            <section id="naming" class="panel settings-section"><div class="panel-heading"><div><h3>命名与智能整理</h3><p>命名先预览；模型不直接执行文件操作</p></div></div><label class="setting-row"><span><b>AI 命名建议</b><small>离线备份不依赖 AI；执行前仍需确认</small></span><select v-model="settings.aiProvider" class="select-field small-select" @change="updateSetting('aiProvider',settings.aiProvider)"><option>本地规则优先</option><option>Ollama</option><option>OpenAI 兼容 API</option></select></label><div class="setting-row"><span><b>当前命名预设</b><small>{{ presets.find(p=>p.isDefault)?.pattern || '日期 / 事件 / 设备' }}</small></span><button class="text-button" @click="setPage('presets')">管理预设</button></div></section>
            <section id="quality" class="panel settings-section"><div class="panel-heading"><div><h3>影像质量与相似度</h3><p>候选只标记和隔离，不静默删除</p></div></div><label class="setting-row"><span><b>自动生成质量建议</b><small>照片/视频分别分析，不支持解码的素材仍可备份</small></span><input type="checkbox" :checked="settings.autoAnalyze" @change="updateSetting('autoAnalyze',($event.target as HTMLInputElement).checked)"/></label><label class="setting-row"><span><b>视觉相似度阈值</b><small>当前 {{ settings.similarity }}% · 不等同于 SHA-256 重复</small></span><input class="range-control" type="range" min="50" max="99" :value="settings.similarity" @input="updateSetting('similarity',Number(($event.target as HTMLInputElement).value))"/></label></section>
            <section id="cloud" class="panel settings-section"><div class="panel-heading"><div><h3>百度、夸克与云同步</h3><p>仅接官方允许的 API 与个人账号授权</p></div></div><div class="provider-row"><span class="provider-logo baidu">度</span><span><b>百度网盘</b><small>官方 API / OAuth 待核验</small></span><button class="secondary-button compact" @click="connectProvider('baidu')">连接</button></div><div class="provider-row"><span class="provider-logo quark">夸</span><span><b>夸克网盘</b><small>官方 API 能力待核验</small></span><button class="secondary-button compact" @click="connectProvider('quark')">连接</button></div><div class="setting-row"><span><b>同步速度上限</b><small>0 表示不限制 · {{ settings.maxUploadMbps }} Mbps</small></span><input class="number-control" type="number" min="0" :value="settings.maxUploadMbps" @change="updateSetting('maxUploadMbps',Number(($event.target as HTMLInputElement).value))"/></div><button class="text-button" @click="setPage('account')">管理同步范围与设备 <ArrowRight :size="13"/></button></section>
            <section id="advanced" class="panel settings-section"><div class="panel-heading"><div><h3>通知与高级选项</h3><p>Webhook、带宽和相似度</p></div></div><label class="setting-row"><span><b>Webhook 通知</b><small>真实发送需后端配置并由用户授权</small></span><input type="checkbox" :checked="settings.webhookEnabled" @change="updateSetting('webhookEnabled',($event.target as HTMLInputElement).checked)"/></label><label v-if="settings.webhookEnabled" class="setting-row"><span><b>Webhook 地址</b><small>演示表单；请勿填写真实密钥</small></span><input v-model="settings.webhookUrl" class="text-control" placeholder="https://example.com/hook"/></label><button class="secondary-button" @click="say('高级参数已保存至演示状态。')">保存高级设置</button></section>
            <section id="privacy" class="panel settings-section"><div class="panel-heading"><div><h3>隐私与安全</h3><p>敏感数据默认最小化同步</p></div></div><div class="consent-box"><LockKeyhole :size="17"/><span>原片、访问令牌和精确 GPS/EXIF 不上传影序云端。Agent 不执行任意 Shell。</span></div><label class="check-row"><input type="checkbox" checked disabled/>有副作用操作逐次展示对象、目标与影响并确认</label></section>
          </div></div>
        </template>
      </div></div>
      <footer class="statusbar"><span><i class="status-dot online"></i> 本机界面演示</span><span>所有数据来自 MockDesktopApi · 尚未连接真实服务</span><span class="statusbar-right"><CircleHelp :size="13"/>帮助与反馈 <i></i> V2 草稿预览</span></footer>
    </section>
    <div v-if="showModal==='clear-history'" class="modal-backdrop"><section class="confirm-modal"><button class="modal-close" @click="showModal=''">×</button><span class="modal-icon"><Archive :size="22"/></span><h2>清空归档记录？</h2><p>这只会清空演示历史记录，不会删除已备份媒体文件。</p><div class="modal-actions"><button class="secondary-button" @click="showModal=''">取消</button><button class="danger-button" @click="clearHistory">确认清空</button></div></section></div>
  </div>
</template>

<script lang="ts">
import { defineComponent, h } from 'vue'
const PageHeading = defineComponent({ props:{eyebrow:String,title:String,description:String}, setup(props,{slots}) { return () => h('header',{class:'page-heading-row'},[h('div',[h('span',{class:'eyebrow'},props.eyebrow),h('h1',props.title),h('p',props.description)]),slots.default?.()]) } })
const PackageGlyph = defineComponent({ setup(){return()=>h('span',{class:'package-glyph','aria-hidden':'true'},'▣')} })
export default { components:{ PageHeading, PackageGlyph } }
</script>
