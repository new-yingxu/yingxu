<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { AgentMatch, LocalFileActionService, MediaKind, QuarantineItem } from '../api/contracts'
import type { LocalFileActionPreview, LocalFileActionReceipt } from '../api/local-file-actions'

const props = defineProps<{ service: LocalFileActionService; matches: AgentMatch[]; demo?: boolean }>()
const localMedia = computed(() => props.matches.flatMap((match) => {
  if (match.kind === 'local_media') return [{ mediaId: match.media.mediaId, name: match.media.name, kind: match.media.kind, qualityState: match.media.qualityState }]
  if (match.kind === 'media') return [{ mediaId: match.media.id, name: match.media.name, kind: match.media.kind, qualityState: match.media.qualityState }]
  return []
}))
const quarantine = ref<QuarantineItem[]>([])
const preview = ref<LocalFileActionPreview | null>(null)
const receipt = ref<LocalFileActionReceipt | null>(null)
const renameMediaId = ref<string | null>(null)
const newName = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const now = ref(Date.now())
let expiryTicker: ReturnType<typeof setInterval> | undefined
const previewExpired = computed(() => {
  if (!preview.value) return false
  const expiry = preview.value.expiresAtMs ?? (preview.value.expiresAt ? Date.parse(preview.value.expiresAt) : NaN)
  return Number.isFinite(expiry) && expiry <= now.value
})
const expiryText = computed(() => {
  if (!preview.value) return ''
  const expiry = preview.value.expiresAtMs ?? (preview.value.expiresAt ? Date.parse(preview.value.expiresAt) : NaN)
  if (!Number.isFinite(expiry)) return '未提供有效期'
  return `${new Date(expiry).toLocaleString('zh-CN', { hour12: false })}（${Math.max(0, Math.ceil((expiry - Date.now()) / 60000))} 分钟后失效）`
})
const statusLabel: Record<LocalFileActionReceipt['status'], string> = {
  succeeded: '已完成', partial: '部分完成，请核对来源与目标', completed_unindexed: '文件已改名，资料库索引待重新扫描',
  failed: '执行失败', declined: '已取消', expired: '预览已过期', cancelled: '已取消',
}
const quarantineStateLabel: Record<string, string> = {
  isolated: '待处理', cleanup_pending: '等待清理确认', cleanup_cancelled: '已取消清理，副本保留',
  cleanup_failed: '清理失败，副本保留', trash_pending: '正在移入系统废纸篓', trashed: '已移入系统废纸篓',
  trash_uncertain: '废纸篓状态待核对', restored: '已从系统废纸篓放回',
}
function quarantineLabel(state: string) { return quarantineStateLabel[state] ?? state }

async function refreshQuarantine() {
  try { quarantine.value = await props.service.listQuarantine() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
}
function fileName(match: { name: string }) { return match.name }
function kindOf(match: { kind: MediaKind }): MediaKind { return match.kind }
function beginRename(match: { mediaId: string; name: string }) {
  error.value = ''; notice.value = ''; receipt.value = null
  renameMediaId.value = match.mediaId
  newName.value = match.name
}
async function prepareRename(mediaId: string) {
  if (busy.value || !newName.value.trim()) return
  busy.value = true; error.value = ''; receipt.value = null
  try {
    preview.value = await props.service.prepare({ tool: 'rename_item', mediaId, newName: newName.value.trim() })
    renameMediaId.value = null
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function prepareMove(mediaId: string) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''; receipt.value = null
  try {
    const grant = await props.service.selectDirectory()
    if (!grant) { notice.value = '已取消选择目标目录。'; return }
    preview.value = await props.service.prepare({ tool: 'move_item', mediaId, destinationGrantId: grant.grantId })
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function stageCandidate(mediaId: string) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''; receipt.value = null
  try {
    const result = await props.service.stageCandidate(mediaId)
    notice.value = `已将“${result.name}”复制到待处理废片；原始素材保持不变。`
    await refreshQuarantine()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function prepareCleanup(item: QuarantineItem) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''; receipt.value = null
  try { preview.value = await props.service.prepare({ tool: 'cleanup_quarantine', quarantineId: item.id }) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function decide(accepted: boolean) {
  if (!preview.value || busy.value) return
  const current = preview.value
  busy.value = true; error.value = ''; receipt.value = null
  try {
    receipt.value = await props.service.confirm(current.actionId, accepted)
    preview.value = null
    renameMediaId.value = null
    await refreshQuarantine()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); preview.value = null }
  finally { busy.value = false }
}
onMounted(() => { expiryTicker = setInterval(() => { now.value = Date.now() }, 1000); void refreshQuarantine() })
onUnmounted(() => { if (expiryTicker) clearInterval(expiryTicker) })
</script>

<template>
  <section class="local-file-actions" aria-label="本机 Agent 文件操作">
    <header>
      <div><h3>本机文件操作<span v-if="demo">（演示）</span></h3><p v-if="demo">当前使用合成素材演示操作预览与回执，不会修改任何本地文件。</p><p v-else>先由你选择素材或隔离副本，再查看本机生成的操作预览；每次改名、移动或清理都要单独确认。</p></div>
      <button class="secondary-button compact" :disabled="busy" @click="refreshQuarantine">刷新隔离列表</button>
    </header>

    <div v-if="localMedia.length" class="local-action-files">
      <article v-for="match in localMedia" :key="match.mediaId" class="local-action-file">
        <div><b>{{ fileName(match) }}</b><small>{{ kindOf(match) }} · {{ match.qualityState }} · {{ match.mediaId }}</small></div>
        <div class="local-action-controls">
          <template v-if="renameMediaId === match.mediaId">
            <input v-model="newName" aria-label="新的文件名" maxlength="255" @keydown.enter.prevent="prepareRename(match.mediaId)" />
            <button class="secondary-button compact" :disabled="busy || !newName.trim()" @click="prepareRename(match.mediaId)">预览改名</button>
            <button class="text-button" :disabled="busy" @click="renameMediaId = null">取消</button>
          </template>
          <template v-else>
            <button class="text-button" :disabled="busy || !!preview" @click="beginRename(match)">重命名</button>
            <button class="text-button" :disabled="busy || !!preview" @click="prepareMove(match.mediaId)">移动到…</button>
            <button class="text-button" :disabled="busy || !!preview || match.qualityState !== 'review'" :title="match.qualityState === 'review' ? '原片保留，只复制一份到隔离目录' : '只有待复核素材可以隔离'" @click="stageCandidate(match.mediaId)">复制到待处理废片</button>
          </template>
        </div>
      </article>
    </div>
    <p v-else class="empty-note">在上方本机 Agent 检索到素材后，可从结果中选择需要处理的项目。</p>

    <div v-if="quarantine.length" class="quarantine-list">
      <h4>待处理废片（原始素材仍保留）</h4>
      <article v-for="item in quarantine" :key="item.id" class="local-action-file">
        <div>
          <b>{{ item.name }}</b>
          <small>{{ quarantineLabel(item.state) }} · {{ item.bytes.toLocaleString() }} bytes · SHA-256 {{ item.sha256 }}</small>
          <p v-if="item.restoreViaSystemTrash || item.state === 'trashed' || item.state === 'trash_uncertain'" class="restore-boundary" role="status">
            如需恢复隔离副本，请在 Finder 废纸篓或 Windows 回收站中使用“放回/还原”；之后刷新隔离列表，影序会校验 SHA-256 并更新状态。原始素材始终保留。
          </p>
        </div>
        <button class="text-button" :disabled="busy || !!preview || !['isolated', 'cleanup_cancelled', 'cleanup_failed'].includes(item.state)" @click="prepareCleanup(item)">预览清理副本</button>
      </article>
    </div>

    <section v-if="preview" class="action-preview" role="group" aria-label="本机文件操作预览">
      <h4>操作预览：{{ preview.tool === 'rename_item' ? '重命名' : preview.tool === 'move_item' ? '移动' : '清理隔离副本' }}</h4>
      <dl>
        <dt>对象</dt><dd>{{ preview.objectName }}</dd>
        <dt v-if="preview.sourcePath">来源</dt><dd v-if="preview.sourcePath">{{ preview.sourcePath }}</dd>
        <dt>目标</dt><dd>{{ preview.targetPath }}</dd>
        <dt>影响</dt><dd>{{ preview.effect }}</dd>
        <dt>有效期</dt><dd>{{ expiryText }}</dd>
        <dt v-if="preview.sha256">SHA-256</dt><dd v-if="preview.sha256">{{ preview.sha256 }}</dd>
      </dl>
      <p v-if="previewExpired" class="error-text">预览已过期。请取消后重新发起，系统不会执行过期操作。</p>
      <p v-else>确认前不会执行此操作。拒绝会留下“已取消”回执。</p>
      <div class="action-buttons"><button class="secondary-button" :disabled="busy" @click="decide(false)">取消操作</button><button class="primary-button" :disabled="busy || previewExpired" @click="decide(true)">{{ demo ? '确认演示操作' : '确认并执行' }}</button></div>
    </section>

    <section v-if="receipt" class="action-receipt" aria-live="polite">
      <h4>执行回执：{{ statusLabel[receipt.status] }}</h4>
      <p>操作编号：{{ receipt.actionId }} · {{ receipt.tool }} · 用户确认：{{ receipt.userConfirmed ? '是' : '否' }}</p>
      <p v-if="receipt.sourcePath">来源：{{ receipt.sourcePath }}（{{ receipt.sourcePreserved ? '仍保留' : '已移除或重命名' }}）</p>
      <p v-if="receipt.targetPath">目标：{{ receipt.targetPath }}（{{ receipt.destinationCreated ? '已创建' : '未创建' }}）</p>
      <p v-if="receipt.sha256">SHA-256：{{ receipt.sha256 }}</p>
      <p v-if="receipt.error" class="error-text">{{ receipt.error.code }}：{{ receipt.error.message }}</p>
      <p v-if="receipt.tool === 'cleanup_quarantine' && receipt.status === 'succeeded'" class="restore-boundary">
        原始素材未动。这份隔离副本仍可通过系统废纸篓恢复；放回后刷新隔离列表即可重新校验并更新状态。
      </p>
      <p>云端执行授权：否</p>
    </section>
    <p v-if="notice" class="notice-text" role="status">{{ notice }}</p>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.local-file-actions{display:grid;gap:12px;width:100%;min-width:0;max-width:100%;box-sizing:border-box;overflow:hidden;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--card)}
.local-file-actions header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;min-width:0}.local-file-actions header>div{min-width:0;flex:1}.local-file-actions header>.secondary-button{flex:none;max-width:100%}.local-file-actions h3,.local-file-actions h4{margin:0 0 6px}.local-file-actions header p,.empty-note{margin:0;color:var(--muted);font-size:12px;line-height:1.6;overflow-wrap:anywhere}
.local-action-files,.quarantine-list{display:grid;gap:8px}.quarantine-list{border-top:1px solid var(--line);padding-top:12px}.local-action-file{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;min-width:0}.local-action-file>div:first-child{display:grid;gap:4px;min-width:0}.local-action-file b,.local-action-file small{overflow-wrap:anywhere}.local-action-file small{color:var(--muted);font-size:11px}.restore-boundary{margin:4px 0 0;color:var(--muted);font-size:11px;line-height:1.55;overflow-wrap:anywhere}.local-action-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}.local-action-controls input{width:min(220px,100%);min-width:100px}.action-preview,.action-receipt{padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface,#f8f8f5)}.action-preview h4,.action-receipt h4{margin:0 0 10px}.action-preview dl{display:grid;grid-template-columns:100px minmax(0,1fr);gap:8px 12px;margin:0}.action-preview dt{font-weight:700}.action-preview dd{margin:0;overflow-wrap:anywhere}.action-preview p,.action-receipt p{margin:10px 0 0;line-height:1.55;overflow-wrap:anywhere}.action-buttons{display:flex;justify-content:flex-end;gap:10px;margin-top:14px}.error-text{color:var(--red,#a53b32)}.notice-text{color:var(--green,#2b7b4f)}
@media(max-width:700px){.local-file-actions header,.local-action-file{align-items:stretch;flex-direction:column}.local-action-controls{justify-content:flex-start}.action-preview dl{grid-template-columns:1fr;gap:4px}.action-preview dd{margin:0 0 8px}.action-buttons{flex-wrap:wrap}}
</style>
