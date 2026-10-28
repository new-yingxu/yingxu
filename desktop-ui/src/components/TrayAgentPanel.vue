<script setup lang="ts">
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { onMounted, onUnmounted, ref } from "vue";
import ModelConsent from "./ModelConsent.vue";
import { createTauriApi } from "../api/tauri";
import type { AgentReply } from "../api/contracts";

type Message = { role: "user" | "assistant"; text: string; reply?: AgentReply; actionId?: string };
type JobStatus = { active: boolean; state?: string; progress?: number; speedBytesPerSecond?: number; remainingSeconds?: number | null };
const api = createTauriApi({ confirm: async () => false });
const input = ref("");
const busy = ref(false);
const consent = ref<InstanceType<typeof ModelConsent> | null>(null);
const pendingQuery = ref("");
const messages = ref<Message[]>([]);
const job = ref<JobStatus>({ active: false });
const jobError = ref("");
let poller: number | undefined;

function rate(value = 0) {
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB/s`;
  return `${value} B/s`;
}
function eta(value: number | null | undefined) {
  if (value == null) return "估算中";
  if (value >= 3600) return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`;
  if (value >= 60) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
  return `${value} 秒`;
}
async function refreshJob() {
  try {
    job.value = await invoke<JobStatus>("tray_job_snapshot");
    jobError.value = "";
  } catch {
    jobError.value = "暂时无法读取本机任务状态";
  }
}
async function send() {
  const query = input.value.trim();
  if (!query || busy.value || !consent.value) return;
  busy.value = true;
  pendingQuery.value = query;
  try {
    await consent.value.submit(query);
  } finally {
    busy.value = false;
  }
}
function appendReply(value: { query: string; reply: AgentReply }) {
  messages.value.push({ role: "user", text: value.query });
  messages.value.push({
    role: "assistant",
    text: value.reply.text,
    reply: value.reply,
    actionId: value.reply.proposedAction?.kind === "open" ? value.reply.proposedAction.actionId : undefined,
  });
  input.value = "";
}
async function decide(message: Message, accepted: boolean) {
  if (!message.actionId) return;
  const actionId = message.actionId;
  message.actionId = undefined;
  try {
    const result = await api.agent.confirm(actionId, accepted);
    if (typeof result === "string") messages.value.push({ role: "assistant", text: result });
    else messages.value.push({ role: "assistant", text: result.text, reply: result });
  } catch {
    messages.value.push({ role: "assistant", text: "本机操作没有返回可验证的成功回执；请在主界面查看 Agent 状态。" });
  }
}
async function openMain() {
  await invoke("tray_open_main");
}
async function quit() {
  await invoke("tray_quit");
}
async function hide() {
  await getCurrentWindow().hide();
}

onMounted(() => {
  void refreshJob();
  poller = window.setInterval(() => void refreshJob(), 1000);
});
onUnmounted(() => {
  if (poller !== undefined) window.clearInterval(poller);
});
</script>

<template>
  <main class="tray-agent-shell">
    <header class="tray-agent-header">
      <div><img src="/yingxu-app-icon.png" alt="" /><span><b>影序</b><small>菜单栏助手</small></span></div>
      <button type="button" aria-label="收起小窗" title="收起" @click="hide">−</button>
    </header>
    <section class="tray-transfer-card" aria-live="polite">
      <div class="tray-section-title"><b>传输进度</b><span>{{ job.active ? (job.state === 'running' ? '进行中' : job.state === 'paused' ? '已暂停' : '排队中') : '空闲' }}</span></div>
      <template v-if="job.active">
        <progress :value="job.progress || 0" max="100" aria-label="传输进度" />
        <div class="tray-progress-meta"><b>{{ Math.round(job.progress || 0) }}%</b><span>{{ rate(job.speedBytesPerSecond) }}</span></div>
        <small>预计剩余 {{ eta(job.remainingSeconds) }}</small>
      </template>
      <p v-else>{{ jobError || "当前没有进行中的备份或传输任务。" }}</p>
    </section>
    <section class="tray-chat-card">
      <div class="tray-section-title"><b>Agent 助手</b><span>逐次确认后运行</span></div>
      <div class="tray-message-list" aria-live="polite">
        <p v-if="messages.length === 0" class="tray-chat-empty">可以查询本机素材和备份任务。照片、完整路径不会自动发到云端。</p>
        <article v-for="(message, index) in messages" :key="index" :class="['tray-message', message.role]">
          <small>{{ message.role === 'user' ? '你' : 'Agent' }}</small>
          <p>{{ message.text }}</p>
          <div v-if="message.reply?.matches.length" class="tray-matches">
            <span v-for="match in message.reply.matches" :key="match.kind === 'local_media' ? match.media.mediaId : match.kind === 'media' ? match.media.id : match.jobId">
              {{ match.kind === 'local_media' ? match.media.name : match.kind === 'media' ? match.media.name : match.name }}
            </span>
          </div>
          <div v-if="message.actionId" class="tray-action-confirm">
            <small>Agent 建议在本机打开：{{ message.reply?.proposedAction?.label }}</small>
            <div><button type="button" @click="decide(message, false)">拒绝</button><button type="button" @click="decide(message, true)">确认打开</button></div>
          </div>
        </article>
      </div>
      <ModelConsent ref="consent" :service="api.model" :query="input" @reply="appendReply" />
      <form class="tray-composer" @submit.prevent="send">
        <input v-model="input" maxlength="500" placeholder="问问本机素材或备份…" />
        <button type="submit" :disabled="busy || !input.trim()">{{ busy ? '等待…' : '发送' }}</button>
      </form>
    </section>
    <footer class="tray-agent-footer">
      <button type="button" @click="openMain">打开主界面</button>
      <button type="button" class="tray-quit" @click="quit">退出影序</button>
    </footer>
  </main>
</template>

<style scoped>
.tray-agent-shell{height:100dvh;box-sizing:border-box;display:flex;flex-direction:column;gap:12px;padding:14px;background:var(--page,#f5f3ed);color:var(--text,#202722);overflow:hidden;font:13px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif}.tray-agent-header{height:38px;flex:none;display:flex;align-items:center;justify-content:space-between}.tray-agent-header>div{display:flex;align-items:center;gap:9px}.tray-agent-header img{width:30px;height:30px;border-radius:8px}.tray-agent-header span{display:grid}.tray-agent-header small,.tray-section-title span,.tray-transfer-card small{color:var(--muted,#77796f);font-size:11px}.tray-agent-header button{border:1px solid var(--line,#e8e4dc);background:white;border-radius:8px;width:30px;height:30px;font-size:20px}.tray-transfer-card,.tray-chat-card{background:var(--card,#fff);border:1px solid var(--line,#e8e4dc);border-radius:14px;padding:13px;box-shadow:0 5px 18px #22200008}.tray-transfer-card{flex:none}.tray-section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}.tray-transfer-card progress{width:100%;height:8px;accent-color:#284034}.tray-progress-meta{display:flex;justify-content:space-between;margin-top:5px}.tray-transfer-card p{margin:0;color:var(--muted,#77796f);font-size:12px}.tray-chat-card{min-height:0;flex:1;display:flex;flex-direction:column;overflow:hidden}.tray-message-list{min-height:100px;flex:1;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:3px 1px}.tray-chat-empty{color:var(--muted,#77796f);font-size:12px;max-width:280px}.tray-message{max-width:94%;padding:8px 10px;border-radius:10px;background:#f3f4ef;overflow-wrap:anywhere}.tray-message.user{align-self:flex-end;background:#e8eee9}.tray-message small{color:#728078;font-size:10px}.tray-message p{margin:3px 0 0;white-space:pre-wrap}.tray-matches{display:grid;gap:4px;margin-top:7px}.tray-matches span{padding:5px 7px;border:1px solid var(--line,#e8e4dc);border-radius:7px;font-size:11px}.tray-action-confirm{margin-top:8px}.tray-action-confirm div{display:flex;gap:7px;margin-top:6px}.tray-action-confirm button,.tray-agent-footer button{border:1px solid var(--line,#e8e4dc);background:white;border-radius:8px;padding:7px 10px;color:inherit}.tray-action-confirm button:last-child{background:#284034;color:white;border-color:#284034}.tray-chat-card :deep(.model-consent-panel){padding:8px 0;margin:6px 0;border:0;border-top:1px solid var(--line,#e8e4dc);border-radius:0;font-size:11px}.tray-chat-card :deep(.model-consent-panel p){font-size:11px;line-height:1.45}.tray-chat-card :deep(.model-status-row){gap:7px}.tray-chat-card :deep(.model-status-row strong){font-size:11px}.tray-chat-card :deep(.model-status-row span){font-size:10px;min-width:0}.tray-chat-card :deep(.model-consent-panel .secondary-button){min-height:30px;font-size:10px}.tray-composer{display:flex;gap:7px;flex:none}.tray-composer input{min-width:0;flex:1;border:1px solid var(--line,#e8e4dc);border-radius:9px;padding:9px 10px;background:white;color:inherit;font:inherit}.tray-composer button{border:0;border-radius:9px;padding:0 12px;background:#284034;color:white;font-weight:600}.tray-composer button:disabled{opacity:.5}.tray-agent-footer{flex:none;display:flex;justify-content:space-between}.tray-agent-footer .tray-quit{color:#a33c32}
</style>
