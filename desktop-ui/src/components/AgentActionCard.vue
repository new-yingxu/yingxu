<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import type { AgentReply } from '../api/contracts'

type ProposedAction = NonNullable<AgentReply['proposedAction']>

const props = defineProps<{ action: ProposedAction; busy?: boolean }>()
const emit = defineEmits<{ decide: [accepted: boolean] }>()
const decline = ref<HTMLButtonElement | null>(null)

const kindLabel: Record<ProposedAction['kind'], string> = {
  open: '打开本机素材',
  rename: '重命名',
  move: '移动',
  upload: '上传',
  cleanup: '清理',
}
const title = computed(() => `确认${kindLabel[props.action.kind] || '本机操作'}`)
const files = computed(() => props.action.objects.join('、') || props.action.label)
const expiresText = computed(() => {
  const raw = props.action.expiresAt
  if (!raw) return '未提供有效期'
  const parsed = Date.parse(raw)
  if (!Number.isFinite(parsed)) return '未提供有效期'
  const minutes = Math.max(0, Math.round((parsed - Date.now()) / 60000))
  return `${new Date(parsed).toLocaleString('zh-CN', { hour12: false })}（约 ${minutes} 分钟内有效）`
})
function keyboard(event: KeyboardEvent) {
  if (event.key === 'Escape' && !props.busy) {
    event.preventDefault()
    emit('decide', false)
  }
}
// The safe answer keeps the keyboard focus: 拒绝 is focused when the card opens.
onMounted(async () => {
  await nextTick()
  decline.value?.focus()
})
</script>
<template>
  <div class="action-confirm-overlay" @keydown="keyboard">
    <section
      class="action-confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="agent-action-title"
    >
      <h2 id="agent-action-title">{{ title }}</h2>
      <dl>
        <dt>文件</dt>
        <dd>{{ files }}</dd>
        <dt>来源</dt>
        <dd>{{ action.source || '本机 Agent 提议' }}</dd>
        <dt>目标</dt>
        <dd>{{ action.target }}</dd>
        <dt>过期时间</dt>
        <dd>{{ expiresText }}</dd>
      </dl>
      <p class="action-confirm-note">
        确认前不会访问文件；拒绝后不会打开任何素材，也不会再次询问。
      </p>
      <div class="action-confirm-actions">
        <button
          ref="decline"
          class="secondary-button"
          :autofocus="true"
          :disabled="busy"
          @click="emit('decide', false)"
        >
          拒绝
        </button>
        <button
          class="primary-button"
          :disabled="busy"
          @click="emit('decide', true)"
        >
          同意
        </button>
      </div>
    </section>
  </div>
</template>
<style scoped>
.action-confirm-overlay{position:fixed;inset:0;background:#17291fc0;z-index:150;display:grid;place-items:center;padding:20px}
.action-confirm-dialog{width:min(560px,100%);max-height:calc(100dvh - 40px);overflow:auto;padding:24px;border-radius:20px;background:var(--card);box-shadow:0 18px 80px #0004}
.action-confirm-dialog h2{margin:0 0 16px;font-size:16px}
.action-confirm-dialog dt{font-weight:700;margin:12px 0 6px}
.action-confirm-dialog dd{margin:0;overflow-wrap:anywhere;line-height:1.65}
.action-confirm-note{margin:16px 0 0;font-size:12px;color:var(--muted);line-height:1.6}
.action-confirm-actions{display:flex;gap:12px;justify-content:flex-end;margin-top:18px}
.action-confirm-actions button{min-width:104px}
@media(max-width:800px){.action-confirm-dialog{padding:18px}.action-confirm-actions{flex-wrap:wrap}}
</style>
