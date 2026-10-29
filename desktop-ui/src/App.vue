<script setup lang="ts">
import ScanSummary from "./components/ScanSummary.vue";
import NetdiskPanel from "./components/NetdiskPanel.vue";
import ModelConsent from "./components/ModelConsent.vue";
import AgentLocalFileActions from "./components/AgentLocalFileActions.vue";
import OllamaOnboarding from "./components/OllamaOnboarding.vue";
import CloudModelSettings from "./components/CloudModelSettings.vue";
import TrayAgentPanel from "./components/TrayAgentPanel.vue";
import AgentActionCard from "./components/AgentActionCard.vue";
import { canEnterNativeLocalMode, canShowDevDiagnostics, canUseCloudFeatures, enterNativeLocalMode, runCloudAction } from "./api/local-mode";
import { shouldShowLocalAgentAdapterNotice } from "./api/local-agent";
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import type {
  AgentReply,
  AppSettings,
  ArchiveTask,
  AuditResult,
  Device,
  MediaFile,
  NamePreset,
  TaskState,
  Confirmation,
  LocalCapabilities,
  MediaAnalysis,
  AppApi,
  ModelProviderConfig,
  ScanReport, IndexResult,
  NetdiskProvider,
  NetdiskTask,
} from "./api/contracts";
import { createDesktopApi } from "./api";
import { errorText } from "./api/tauri";
import {
  agentModelDisplayLabel,
  agentModelSelection,
  canSendAgent,
  friendlyUiError,
  presetDisplayName as displayPresetName,
} from "./api/ui-safety";
import {
  canControl,
  canEnterStep,
  canRetryTarget,
  summarizeResult,
} from "./api/backup-state";
import { formatBytes } from "./api/format";
import type { RawJob } from "./api/format";
import { checkForUpdates, currentAppVersion, openReleaseUrl } from "./api/version-updates";
import type { UpdateCheckResult } from "./api/version-updates";
import { requestAccountChangeCode, updateAccountPassword, updateAccountUsername } from "./api/nativeAuth";
import {
  applyLibraryPage,
  canLoadNextPage,
  canLoadPreviousPage,
  createLibraryPage,
  describeLibraryPage,
  failLibraryLoad,
  LIBRARY_PAGE_SIZE,
  nextLibraryOffset,
  previousLibraryOffset,
  resetLibraryPage,
  startLibraryLoad,
} from "./api/library-paging";
import type { LibraryPageState } from "./api/library-paging";
import {
  Activity,
  Archive,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Cloud,
  Copy,
  Database,
  FileImage,
  FileVideo,
  Filter,
  Folder,
  HardDrive,
  Image,
  LayoutDashboard,
  LockKeyhole,
  Moon,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sun,
  WandSparkles,
  X,
} from "./icons";

const props = defineProps<{
  createApi?: (confirm: (value: Confirmation) => Promise<boolean>) => AppApi;
  testTransport?: string;
}>();
const isTrayAgentWindow = new URLSearchParams(window.location.search).get("window") === "tray-agent";
const pendingConfirmation = ref<Confirmation | null>(null);
let resolveConfirmation: ((accepted: boolean) => void) | null = null;
const api = (props.createApi || createDesktopApi)(
  (value) =>
    new Promise<boolean>((resolve, reject) => {
      if (resolveConfirmation) {
        reject(new Error("请先处理当前确认"));
        return;
      }
      pendingConfirmation.value = value;
      resolveConfirmation = resolve;
    }),
  async () => true,
);
const isMock = api.mode === "mock";
const localWorkspace = ref(false);
// LocalIntegration enables offline library/backup; Agent model IPC is checked independently.
const canEnterLocalWorkspace = canEnterNativeLocalMode(api.mode, !!api.local);
const showLocalAgentAdapterNotice = shouldShowLocalAgentAdapterNotice(api.mode, !!api.model);
const canUseCloud = computed(() => canUseCloudFeatures(session.value, localWorkspace.value));
const canShowLocalDiagnostics = canShowDevDiagnostics(api.mode, !!api.local, import.meta.env.DEV);
const displayName = computed(
  () =>
    session.value?.displayName ||
    (localWorkspace.value ? "本机模式" : "未登录"),
);
const displayEmail = computed(() => session.value?.email || "云端未登录");
const localCapabilities = ref<LocalCapabilities | null>(null);
const hostStatus = ref("正在检查本地后端");
const appVersion = ref("读取中…");
const updateCheckBusy = ref(false);
const updateCheckResult = ref<UpdateCheckResult | null>(null);
const updateCheckError = ref("");
const updateCheckedAt = ref("");
const operationBusy = ref(false);
const analysis = ref<MediaAnalysis | null>(null);
const analysisTitle = ref("");
const thumbnails = reactive<Record<string, string>>({});
const taskReport = ref<Record<string, unknown> | null>(null);
const healthyReplicas = ref<{ id: string; path: string; verified: boolean }[]>(
  [],
);
const restorePath = ref("");
const namingValues = reactive<Record<string, string>>({
  date: new Date().toLocaleDateString("sv-SE"),
  event: "手动导入",
  device: "本机来源",
  location: "未指定地点",
  kind: "影像",
  custom: "归档",
});
let stopEvents: (() => void) | undefined;
let polling: number | undefined;
let pollingBusy = false;
let thumbnailEpoch = 0;

type Page =
  | "home"
  | "backup"
  | "transfer"
  | "library"
  | "history"
  | "task"
  | "agent"
  | "account"
  | "presets"
  | "health"
  | "delivery"
  | "settings";
const page = ref<Page>("home");
const session = ref<Awaited<ReturnType<typeof api.auth.restore>>>(null);
const loginMode = ref<
  "email-password" | "email-code" | "register" | "forgot-password"
>("email-password");
const loginIdentifier = ref("");
const loginNickname = ref("");
const loginProof = ref("");
const loginError = ref("");
const loginBusy = ref(false);
const accountDialog = ref<"username" | "password" | "">("");
const accountUsernameDraft = ref("");
const accountUsername = ref("");
const accountOldPassword = ref("");
const accountNewPassword = ref("");
const accountConfirmPassword = ref("");
const accountVerificationCode = ref("");
const accountCodeSent = ref(false);
const accountBusy = ref(false);
const accountError = ref("");
const notice = ref("");
const showModal = ref("");
const globalSearchOpen = ref(false);
const globalSearchQuery = ref("");
const libraryQuery = ref("");
const historyQuery = ref("");
const presetEditorOpen = ref(false);
const presetEditingId = ref<string | null>(null);
const presetDraftName = ref("");
const presetDraftPattern = ref("日期 / 事件 / 设备");
const presetToDelete = ref<NamePreset | null>(null);
const taskToRename = ref<ArchiveTask | null>(null);
const taskRenameDraft = ref("");
const filterKind = ref<"all" | "photo" | "video" | "other">("all");
const filterQuality = ref("all");
const filterState = ref<TaskState | "all">("all");
const selectedTaskId = ref("YX-20250422-014");
const taskDetail = ref<ArchiveTask | null>(null);
const tasks = ref<ArchiveTask[]>([]);
const files = ref<MediaFile[]>([]);
const libraryPage = ref<LibraryPageState>(createLibraryPage());
const sources = ref<
  {
    id: string;
    name: string;
    path: string;
    files: number;
    size: string;
    free: string;
  }[]
>([]);
const devices = ref<Device[]>([]);
const presets = ref<NamePreset[]>([]);
const auditResult = ref<AuditResult | null>(null);
const scanBusy = ref(false);
const backupScan = ref<ScanReport|null>(null);
const indexScan = ref<IndexResult|null>(null);
const transferScan = ref<IndexResult|null>(null);
const transferBusy = ref(false);
const transferFiles = ref<MediaFile[]>([]);
const selectedTransfer = ref<string[]>([]);
const transferConfirmSource = ref(false);
const transferTarget = ref(isMock ? "本机硬盘 / 老内容归档" : "");
const backupStep = ref(1);
const backupSource = ref("sd-1");
const backupTargets = ref<string[]>(isMock ? ["local"] : []);
const backupPreset = ref("preset-camera");
const backupTask = ref<ArchiveTask | null>(null);
const agentInput = ref("");
const agentModelConfig = ref<ModelProviderConfig | null>(null);
const agentLocalModels = ref<string[]>([]);
const agentSelectedModel = ref("");
const agentModelBusy = ref(false);
const agentSettingsOpen = ref(false);
const agentActionBusy = ref(false);
const agentConversationKey = ref(0);
const agentNewConversationReview = ref(false);
const agentHasArchivedUnresolvedTurn = ref(false);
const modelConsentRef = ref<{
  submit(query: string): Promise<void>;
  requiresRecovery(): boolean;
  isBusy(): boolean;
} | null>(null);
type AgentMessage = {
  role: "assistant" | "user";
  text: string;
  reply?: AgentReply;
  actionId?: string;
};
const agentMessages = ref<AgentMessage[]>([]);
const agentModelValue = computed(() => agentModelSelection(agentModelConfig.value, localWorkspace.value));
const agentModelLabel = computed(() => agentModelDisplayLabel(agentModelConfig.value, localWorkspace.value));
const agentCanSend = computed(() => canSendAgent(localWorkspace.value, agentModelConfig.value));
const latestAgentMatches = computed(() => {
  for (let index = agentMessages.value.length - 1; index >= 0; index -= 1) {
    const message = agentMessages.value[index];
    if (message.role === "assistant" && message.reply?.matches.length) return message.reply.matches;
  }
  return [];
});
const settings = reactive<AppSettings>({
  theme: "light",
  startOnLogin: true,
  backgroundOnClose: true,
  verify: true,
  autoAnalyze: true,
  similarity: 88,
  maxUploadMbps: 0,
  syncHistory: true,
  syncAgent: true,
  syncThumbnails: true,
  syncPaths: false,
  webhookEnabled: false,
  webhookUrl: "",
  aiProvider: "本地规则优先",
});
const deliveryName = ref("客户交付包 · 春日人像");
const deliveryContact = ref(true);
const deliveryManifest = ref(true);
const deliverySelection = ref<string[]>(isMock ? ["f1", "f3"] : []);
const summary = reactive({
  fileCount: 0,
  totalSize: "",
  destinations: 0,
  reviewCount: 0,
});
const dashboardBaiduProvider = ref<NetdiskProvider | null>(null);
const dashboardBaiduTasks = ref<NetdiskTask[]>([]);
const dashboardBaiduLoading = ref(false);
const dashboardBaiduError = ref("");
let dashboardBaiduLastRefresh = 0;
const dashboardBaiduCounts = computed(() => ({
  queued: dashboardBaiduTasks.value.filter((task) => task.state === "queued").length,
  uploading: dashboardBaiduTasks.value.filter((task) => task.state === "uploading").length,
  verified: dashboardBaiduTasks.value.filter((task) => task.state === "completed" && task.verifiedRemote).length,
}));
const dashboardBaiduStatus = computed(() => {
  if (localWorkspace.value) return { label: "本机模式已停用", detail: "本机模式不会发起网盘请求。", tone: "queued" };
  if (!session.value) return { label: "登录后连接", detail: "登录影序账号后可查看授权和上传任务。", tone: "queued" };
  if (!api.netdisk) return { label: "当前版本不可用", detail: "当前运行环境未提供原生网盘服务。", tone: "failed" };
  if (dashboardBaiduLoading.value && !dashboardBaiduProvider.value) return { label: "正在读取", detail: "正在读取百度网盘授权状态。", tone: "queued" };
  if (dashboardBaiduError.value) return { label: "状态暂不可读", detail: dashboardBaiduError.value, tone: "failed" };
  const provider = dashboardBaiduProvider.value;
  if (!provider) return { label: "尚未连接", detail: "当前没有可用的百度网盘授权记录。", tone: "queued" };
  if (provider.approval !== "approved" || !provider.realUploadAvailable)
    return { label: "上传暂未开放", detail: "需先满足百度网盘官方应用权限要求。", tone: "queued" };
  return provider.authorized
    ? { label: "已连接", detail: "上传前仍需选择并确认已校验的本地副本。", tone: "success" }
    : { label: "待连接", detail: "连接个人百度网盘后可查看上传队列。", tone: "queued" };
});
const latestDashboardBaiduTask = computed(() => dashboardBaiduTasks.value[0] || null);
const navGroups = [
  {
    title: "工作区",
    items: [
      { id: "home" as Page, label: "工作台", icon: LayoutDashboard },
      { id: "backup" as Page, label: "复制新内容", icon: ArrowDownToLine },
      { id: "transfer" as Page, label: "转移老内容", icon: Folder },
      { id: "library" as Page, label: "影像资料库", icon: Image },
      { id: "history" as Page, label: "归档记录", icon: Archive },
    ],
  },
  {
    title: "工具",
    items: [
      { id: "agent" as Page, label: "Agent 助手", icon: Sparkles },
      { id: "presets" as Page, label: "导入预设", icon: WandSparkles },
      { id: "health" as Page, label: "恢复与健康审计", icon: ShieldCheck },
      { id: "delivery" as Page, label: "交付包", icon: FileImage },
    ],
  },
];
const pageLabels: Record<Page, string> = {
  home: "工作台",
  backup: "复制新内容",
  transfer: "转移老内容",
  library: "影像资料库",
  history: "归档记录",
  task: "任务详情",
  agent: "Agent 助手",
  account: "账号与设备",
  presets: "导入预设",
  health: "恢复与健康审计",
  delivery: "交付包",
  settings: "偏好设置",
};
const visibleFiles = computed(() => files.value);
/** The newest proposal that still waits for an explicit local decision. */
const pendingAgentAction = computed(() => {
  for (let index = agentMessages.value.length - 1; index >= 0; index -= 1) {
    const message = agentMessages.value[index];
    if (message.actionId && message.reply?.proposedAction) return message;
  }
  return null;
});
const selectedTask = computed(
  () =>
    taskDetail.value ||
    tasks.value.find((task) => task.id === selectedTaskId.value) ||
    null,
);
const resultSummary = computed(() =>
  selectedTask.value ? summarizeResult(selectedTask.value) : null,
);
const wizardContext = computed(() => ({
  scanned: sources.value.length > 0,
  targets: backupTargets.value.length,
  busy: operationBusy.value || scanBusy.value,
}));
const activeTitle = computed(() => pageLabels[page.value]);
const globalSearchItems = computed(() => [
  ...navGroups.flatMap((group) =>
    group.items.map((item) => ({ ...item, group: group.title })),
  ),
  ...(canUseCloud.value ? [{
    id: "account" as Page,
    label: "账号与设备",
    group: "工具",
    icon: LockKeyhole,
  }] : []),
  { id: "settings" as Page, label: "偏好设置", group: "工具", icon: Settings2 },
]);
const globalSearchMatches = computed(() => {
  const q = globalSearchQuery.value.trim().toLocaleLowerCase();
  return q
    ? globalSearchItems.value.filter((item) =>
        item.label.toLocaleLowerCase().includes(q),
      )
    : [];
});

watch(globalSearchQuery, (value) => {
  const exact = globalSearchItems.value.find(
    (item) => item.label === value.trim(),
  );
  if (exact) chooseGlobalSearch(exact.id);
});

const resultRows = computed(() => {
  if (taskReport.value) {
    const job = taskReport.value.job as RawJob | undefined;
    return job
      ? [
          { label: "状态", value: taskStateLabel(job.state) },
          {
            label: "文件",
            value: `${job.files} 项 · ${formatBytes(job.totalBytes)}`,
          },
          { label: "来源", value: job.source },
          {
            label: "完整性",
            value: job.verify
              ? "已启用 SHA-256，逐目标结果如下"
              : "未执行目标校验",
          },
          {
            label: "逐目标校验",
            value: job.targets
              .map(
                (t) =>
                  `${t.path}：通过 ${t.verified}、失败 ${t.failed}、跳过 ${t.skipped}`,
              )
              .join("\n"),
          },
        ]
      : [];
  }
  const result = analysis.value;
  if (!result) return [];
  const metadata = result.metadata || {};
  const video = (metadata.video || {}) as Record<string, unknown>;
  const quality = (metadata.quality || {}) as Record<string, unknown>;
  return [
    {
      label: "解码 / 抽帧",
      value: String(metadata.decodeStatus || result.previewSource || "未提供"),
    },
    {
      label: "缩略图",
      value: result.thumbnailAvailable
        ? "已生成本地预览"
        : "无可用预览，不等于原片损坏",
    },
    {
      label: "RAW 方式",
      value:
        result.fullDecodePerformed === false
          ? "内嵌预览，未做 RAW 显影"
          : "不适用",
    },
    {
      label: "视频信息",
      value: video.width
        ? `${video.width} × ${video.height} · ${video.fps || "未知"} fps · ${video.durationMs || "未知"} ms`
        : "不适用",
    },
    { label: "抽帧数量", value: String(metadata.framesAnalyzed ?? "不适用") },
    { label: "结果完整性", value: metadata.decodeStatus === "frames_sampled" && metadata.framesAnalyzed === 0 ? "有效逐帧信号为 0，结果不完整；不能据此判断或清理" : "仅供人工复核，不自动清理" },
    {
      label: "抽帧来源",
      value: String(result.frameSource || metadata.frameSource || "").startsWith("ffmpeg_") ? `ffmpeg 本地抽帧（${result.frameSource || metadata.frameSource}）` : String(result.frameSource || metadata.frameSource || "不适用"),
    },
    {
      label: "帧定位方式",
      value: Array.isArray(quality.frameSignals) ? quality.frameSignals.map((s: Record<string, unknown>) => `${s.frame}: ${s.frameMode === "keyframe" ? "关键帧" : s.frameMode === "nearest" ? "邻近帧" : String(s.frameMode || "未提供")}`).join("；") || "无逐帧信号" : "不适用",
    },
    {
      label: "上传状态",
      value:
        result.framesUploaded === false || metadata.framesUploaded === false
          ? "抽帧未上传"
          : "本地分析，未调用云端视觉服务",
    },
    {
      label: "质量候选",
      value:
        Array.isArray(quality.candidates) && quality.candidates.length
          ? quality.candidates.join("、")
          : "未标出候选，不代表质量已获人工确认",
    },
    { label: "判断边界", value: "阈值未校准，不能据此自动清理" },
    {
      label: "补充原因",
      value: String(metadata.decodeReason || metadata.previewReason || "—"),
    },
  ];
});
watch(pendingConfirmation, async (value) => {
  if (value) {
    await nextTick();
    document
      .querySelector<HTMLButtonElement>(
        ".integration-confirm .secondary-button",
      )
      ?.focus();
  }
});
function modalKeys(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    finishConfirmation(false);
    return;
  }
  if (event.key !== "Tab") return;
  const dialog = event.currentTarget as HTMLElement;
  const fields = [
    ...dialog.querySelectorAll<HTMLElement>(
      'button,input,select,summary,[tabindex="0"]',
    ),
  ].filter((e) => e.offsetParent !== null && !e.hasAttribute("disabled"));
  if (!fields.length) return;
  const index = fields.indexOf(document.activeElement as HTMLElement);
  if (event.shiftKey && index <= 0) {
    event.preventDefault();
    fields.at(-1)?.focus();
  } else if (!event.shiftKey && index === fields.length - 1) {
    event.preventDefault();
    fields[0].focus();
  }
}
function say(text: string) {
  notice.value = text;
  window.setTimeout(() => {
    if (notice.value === text) notice.value = "";
  }, 6500);
}
function showError(error: unknown) {
  const friendlyMessage = friendlyUiError(error);
  if (friendlyMessage) {
    say(friendlyMessage);
    return;
  }
  say(errorText(error));
}
function finishConfirmation(accepted: boolean) {
  const resolve = resolveConfirmation;
  resolveConfirmation = null;
  pendingConfirmation.value = null;
  resolve?.(accepted);
}
async function runAction(action: () => Promise<void>) {
  if (operationBusy.value) return;
  operationBusy.value = true;
  try {
    await action();
  } catch (error) {
    showError(error);
  } finally {
    operationBusy.value = false;
  }
}
async function checkAppUpdates() {
  if (updateCheckBusy.value) return;
  updateCheckBusy.value = true;
  updateCheckError.value = "";
  try {
    const result = await checkForUpdates();
    appVersion.value = result.currentVersion;
    updateCheckResult.value = result;
    updateCheckedAt.value = new Date().toLocaleString();
  } catch (error) {
    updateCheckError.value = error instanceof Error ? error.message : "版本查询暂时不可用，请稍后重试。";
  } finally {
    updateCheckBusy.value = false;
  }
}
async function openAppUpdate() {
  const result = updateCheckResult.value;
  if (result?.kind !== "available") return;
  try {
    await openReleaseUrl(result.downloadUrl || result.releaseUrl);
  } catch (error) {
    showError(error);
  }
}
async function openReleaseList() {
  try {
    await openReleaseUrl("https://github.com/new-yingxu/yingxu/releases");
  } catch (error) {
    showError(error);
  }
}
async function loadData() {
  const [snapshot, settingData, presetData, page] = await Promise.all([
    api.workbench.snapshot(),
    api.settings.get(),
    api.presets.list(),
    api.library.searchPage("", "all", "all", LIBRARY_PAGE_SIZE, 0),
  ]);
  tasks.value = snapshot.tasks;
  Object.assign(summary, snapshot);
  Object.assign(settings, settingData);
  presets.value = presetData;
  const next = applyLibraryPage(libraryPage.value, page);
  libraryPage.value = next;
  files.value = next.items;
  if (!presetData.some((p) => p.id === backupPreset.value))
    backupPreset.value =
      presetData.find((p) => p.isDefault)?.id || presetData[0]?.id || "";
  if (isMock) devices.value = await api.account.devices();
  document.documentElement.dataset.theme = settings.theme;
  void refreshDashboardBaidu(true);
}
async function refreshDashboardBaidu(force = false) {
  if (!api.netdisk || !session.value || localWorkspace.value) {
    dashboardBaiduProvider.value = null;
    dashboardBaiduTasks.value = [];
    dashboardBaiduError.value = "";
    dashboardBaiduLastRefresh = 0;
    return;
  }
  if (dashboardBaiduLoading.value || (!force && Date.now() - dashboardBaiduLastRefresh < 8_000)) return;
  dashboardBaiduLoading.value = true;
  dashboardBaiduLastRefresh = Date.now();
  try {
    const [providers, tasks] = await Promise.all([
      api.netdisk.providers(),
      api.netdisk.tasks(),
    ]);
    dashboardBaiduProvider.value = providers.find((provider) => provider.id === "baidu") || null;
    // The native API returns tasks in update order; preserve it for the latest-task summary.
    dashboardBaiduTasks.value = tasks.filter((task) => task.provider === "baidu");
    dashboardBaiduError.value = "";
  } catch (error) {
    dashboardBaiduError.value = errorText(error);
  } finally {
    dashboardBaiduLoading.value = false;
  }
}
async function enterLocalWorkspace() {
  if (!canEnterLocalWorkspace) return;
  await runAction(async () => {
    const entered = await enterNativeLocalMode({
      mode: api.mode,
      hasLocalDataAdapter: !!api.local,
      loadLocalData: loadData,
      setSession: (value) => { session.value = value; },
      setEnabled: (value) => { localWorkspace.value = value; },
    });
    if (entered) {
      page.value = "home";
      say("已进入本机模式：不登录、不云同步，数据仅在此设备处理。");
    }
  });
}
function onAppError(event: Event) {
  showError((event as CustomEvent).detail);
}
function onUnhandled(event: PromiseRejectionEvent) {
  event.preventDefault();
  showError(event.reason);
}
onMounted(async () => {
  window.addEventListener("yingxu-error", onAppError);
  window.addEventListener("unhandledrejection", onUnhandled);
  void currentAppVersion().then((version) => { appVersion.value = version; }).catch(() => {});
  try {
    session.value = await api.auth.restore();
    if (session.value) await loadData();
  } catch (error) {
    showError(error);
  }
  if (api.local) {
    try {
      const health = await api.local.health();
      hostStatus.value =
        health.workerReady === false
          ? "本地 worker 未就绪"
          : "已连接 Rust / SQLite";
    } catch (error) {
      hostStatus.value = errorText(error);
    }
    try {
      localCapabilities.value = await api.local.capabilities();
    } catch (error) {
      hostStatus.value += `；扩展能力不可用：${errorText(error)}`;
    }
    try {
      stopEvents = await api.local.subscribe((task) => {
        tasks.value = [task, ...tasks.value.filter((t) => t.id !== task.id)];
        if (selectedTaskId.value === task.id) taskDetail.value = task;
      }, showError, say);
    } catch (error) {
      say(`使用轮询：${errorText(error)}`);
    }
    polling = window.setInterval(async () => {
      if (
        pollingBusy ||
        document.hidden ||
        (!session.value && !localWorkspace.value)
      )
        return;
      pollingBusy = true;
      try {
        if (page.value === "task" && selectedTaskId.value) {
          taskDetail.value = await api.history.detail(selectedTaskId.value);
          tasks.value = tasks.value.map((t) =>
            t.id === taskDetail.value?.id ? taskDetail.value! : t,
          );
        } else if (page.value === "home") {
          const snapshot = await api.workbench.snapshot();
          tasks.value = snapshot.tasks;
          Object.assign(summary, snapshot);
          await refreshDashboardBaidu();
        }
      } catch (error) {
        showError(error);
      } finally {
        pollingBusy = false;
      }
    }, 1200);
  }
});
onUnmounted(() => {
  thumbnailEpoch++;
  stopEvents?.();
  if (polling) clearInterval(polling);
  window.removeEventListener("yingxu-error", onAppError);
  window.removeEventListener("unhandledrejection", onUnhandled);
  finishConfirmation(false);
});
async function signIn() {
  if (localWorkspace.value) return;
  loginError.value = "";
  if (!loginIdentifier.value.trim()) {
    loginError.value = "请填写邮箱或用户名。";
    return;
  }
  if (!loginProof.value.trim()) {
    loginError.value = "请填写密码或邮箱验证码。";
    return;
  }
  loginBusy.value = true;
  try {
    const loginMethod = loginMode.value === "email-code" ? "email-code" : "email-password";
    session.value = await api.auth.login(
      loginMethod,
      loginIdentifier.value,
      loginProof.value || "qr-confirmed",
    );
    await loadData();
    page.value = "home";
  } catch (error) {
    const message = errorText(error);
    if (/\b(USER_NOT_FOUND|user_not_found|not_found)\b/.test(message)) {
      loginMode.value = "register";
      loginProof.value = "";
      loginError.value = "此邮箱尚未注册，已为你切换到注册页面。";
      return;
    }
    loginError.value = message;
  } finally {
    loginBusy.value = false;
  }
}
function showRegistration() {
  loginMode.value = "register";
  loginProof.value = "";
  loginError.value = "";
}
function showPasswordReset() {
  loginMode.value = "forgot-password";
  loginProof.value = "";
  loginError.value = "";
}
function submitAuthView() {
  if (loginMode.value === "email-password" || loginMode.value === "email-code") {
    void signIn();
    return;
  }
  loginError.value = loginMode.value === "forgot-password"
    ? "密码重置服务尚未接入；当前不会发送邮件或修改账号。"
    : "邮箱注册服务尚未接入；当前不会创建账号。";
}
async function logout() {
  if (localWorkspace.value) {
    localWorkspace.value = false;
    session.value = null;
    page.value = "home";
    return;
  }
  await api.auth.logout();
  session.value = null;
  page.value = "home";
  loginProof.value = "";
  loginIdentifier.value = "";
}
function openAccountDialog(kind: "username" | "password") {
  accountDialog.value = kind;
  accountError.value = "";
  accountUsernameDraft.value = "";
  accountOldPassword.value = "";
  accountNewPassword.value = "";
  accountConfirmPassword.value = "";
  accountVerificationCode.value = "";
  accountCodeSent.value = false;
}
function closeAccountDialog() {
  accountDialog.value = "";
  accountError.value = "";
  accountOldPassword.value = "";
  accountNewPassword.value = "";
  accountConfirmPassword.value = "";
  accountVerificationCode.value = "";
}
async function sendAccountChangeCode() {
  accountError.value = "";
  if (isMock) {
    accountError.value = "浏览器预览不会发送真实验证码；请在已登录的桌面端使用此功能。";
    return;
  }
  accountBusy.value = true;
  try {
    await requestAccountChangeCode();
    accountCodeSent.value = true;
    say(`验证码已发送至 ${displayEmail.value}，5 分钟内有效。`);
  } catch (error) {
    accountError.value = errorText(error);
  } finally {
    accountBusy.value = false;
  }
}
async function saveAccountUsername() {
  const username = accountUsernameDraft.value.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._:+ @-]{1,47}$/.test(username)) {
    accountError.value = "用户名需为 2 至 48 位，并以字母或数字开头。";
    return;
  }
  if (isMock) {
    accountError.value = "浏览器预览不连接账户服务，未修改真实用户名。";
    return;
  }
  accountBusy.value = true;
  accountError.value = "";
  try {
    await updateAccountUsername(username);
    accountUsername.value = username;
    closeAccountDialog();
    say("登录用户名已更新；退出登录后请使用新用户名登录。");
  } catch (error) {
    accountError.value = errorText(error);
  } finally {
    accountBusy.value = false;
  }
}
async function saveAccountPassword() {
  const password = accountNewPassword.value;
  if (!accountCodeSent.value) {
    accountError.value = "请先获取邮箱验证码。";
    return;
  }
  if (accountOldPassword.value.length === 0) {
    accountError.value = "请输入当前密码。";
    return;
  }
  if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,64}$/.test(password)) {
    accountError.value = "新密码需为 8 至 64 位，并包含大小写字母、数字和特殊字符。";
    return;
  }
  if (password !== accountConfirmPassword.value) {
    accountError.value = "两次输入的新密码不一致。";
    return;
  }
  if (!/^\d{6}$/.test(accountVerificationCode.value)) {
    accountError.value = "请输入 6 位邮箱验证码。";
    return;
  }
  if (isMock) {
    accountError.value = "浏览器预览不连接账户服务，未修改真实密码。";
    return;
  }
  accountBusy.value = true;
  accountError.value = "";
  try {
    await updateAccountPassword(accountOldPassword.value, password, accountVerificationCode.value);
    closeAccountDialog();
    say("登录密码已更新。其他设备可能需要重新登录。" );
  } catch (error) {
    accountError.value = errorText(error);
  } finally {
    accountBusy.value = false;
    accountOldPassword.value = "";
    accountNewPassword.value = "";
    accountConfirmPassword.value = "";
    accountVerificationCode.value = "";
  }
}
async function scanSource() {
  scanBusy.value = true;
  try {
    const scanned = await api.backup.scan();
    sources.value = scanned.sources;
    backupScan.value = scanned.scanReport || null;
    backupSource.value = sources.value[0]?.path || "";
    say(
      isMock
        ? "扫描完成（演示数据）。"
        : `只读扫描完成：${sources.value[0]?.files || 0} 个文件。`,
    );
  } catch (error) {
    showError(error);
  } finally {
    scanBusy.value = false;
  }
}
async function chooseBackupDestination() {
  if (api.local)
    try {
      const path = await api.local.chooseDirectory("destination");
      if (!backupTargets.value.includes(path)) backupTargets.value.push(path);
    } catch (error) {
      showError(error);
    }
}
async function chooseTransferDestination() {
  if (api.local)
    try {
      transferTarget.value = await api.local.chooseDirectory("destination");
    } catch (error) {
      showError(error);
    }
}
async function startBackup() {
  await runAction(async () => {
    const source = sources.value[0]?.path;
    if (!source) {
      say("请先扫描来源");
      return;
    }
    const payload = {
      sourceId: source,
      destinations: backupTargets.value,
      presetId: backupPreset.value,
      verify: settings.verify,
      nameValues: { ...namingValues },
    };
    const task = api.local
      ? await api.local.startBackup(payload)
      : await api.backup.start(payload);
    backupTask.value = task;
    tasks.value = [task, ...tasks.value.filter((t) => t.id !== task.id)];
    page.value = "task";
    selectedTaskId.value = task.id;
    taskDetail.value = task;
    say(isMock ? "演示任务已启动。" : "本地任务已排队，等待实际执行。");
  });
}
async function controlTask(action: "pause" | "resume" | "cancel" | "retry") {
  await runAction(async () => {
    if (!selectedTask.value) return;
    taskDetail.value = await api.backup.control(selectedTask.value.id, action);
    say(`任务状态：${taskDetail.value.phase}`);
  });
}
async function openTask(task: ArchiveTask) {
  selectedTaskId.value = task.id;
  taskDetail.value = await api.history.detail(task.id);
  page.value = "task";
}
async function refreshHistory() {
  tasks.value = await api.history.list(historyQuery.value, filterState.value);
}
async function loadThumbnails(list: MediaFile[], epoch: number) {
  if (!api.local) return;
  const pending = list
    .filter((file) => file.kind !== "other" && !thumbnails[file.id])
    .slice(0, 24);
  for (let i = 0; i < pending.length; i += 4) {
    if (epoch !== thumbnailEpoch) return;
    await Promise.allSettled(
      pending.slice(i, i + 4).map(async (file) => {
        const image = await api.local!.thumbnail(file.id);
        if (epoch === thumbnailEpoch) thumbnails[file.id] = image;
      }),
    );
  }
}
async function loadLibraryPage(offset: number, append: boolean) {
  const epoch = ++thumbnailEpoch;
  const base = append
    ? libraryPage.value
    : resetLibraryPage(libraryPage.value);
  libraryPage.value = startLibraryLoad(base);
  try {
    const page = await api.library.searchPage(
      libraryQuery.value,
      filterKind.value,
      filterQuality.value,
      libraryPage.value.limit,
      offset,
    );
    if (epoch !== thumbnailEpoch) return;
    const next = applyLibraryPage(libraryPage.value, page);
    libraryPage.value = next;
    files.value = next.items;
    await loadThumbnails(next.items, epoch);
  } catch (error) {
    if (epoch !== thumbnailEpoch) return;
    libraryPage.value = failLibraryLoad(
      libraryPage.value,
      errorText(error),
    );
    showError(error);
  }
}
async function searchFiles() {
  await loadLibraryPage(0, false);
}
async function loadNextPage() {
  if (!canLoadNextPage(libraryPage.value)) return;
  await loadLibraryPage(nextLibraryOffset(libraryPage.value), true);
}
async function loadPreviousPage() {
  if (!canLoadPreviousPage(libraryPage.value)) return;
  await loadLibraryPage(previousLibraryOffset(libraryPage.value), false);
}
async function inspectTransfer() {
  transferBusy.value = true;
  try {
    transferFiles.value = await api.transfer.inspect("", transferTarget.value);
    transferScan.value = api.local?.lastIndexReport() || null;
    selectedTransfer.value = transferFiles.value
      .filter((f) => f.kind !== "other")
      .map((f) => f.id);
    say("只读索引完成；尚未开始写入。");
  } catch (error) {
    showError(error);
  } finally {
    transferBusy.value = false;
  }
}
async function beginTransfer() {
  await runAction(async () => {
    const task = await api.transfer.start(
      selectedTransfer.value,
      transferTarget.value,
      transferConfirmSource.value,
    );
    taskDetail.value = task;
    selectedTaskId.value = task.id;
    tasks.value = [task, ...tasks.value];
    page.value = "task";
    say(
      isMock ? "转移演示已启动。" : "副本已排队；来源暂存需要完成后再次确认。",
    );
  });
}
async function queueBaidu(file: MediaFile) {
  if (!api.netdisk) return;
  await runAction(async () => {
    const allowed = await runCloudAction(session.value, localWorkspace.value, async () => {
      await api.netdisk!.enqueue("baidu", file);
      say("已加入上传队列，尚未上传；请到偏好设置确认上传。");
    });
    if (!allowed) say("本机模式已禁用网盘上传；不会发送云端请求。");
  });
}
async function indexLibrary() {
  if (api.local)
    await runAction(async () => {
      const result = await api.local!.indexSource();
      indexScan.value = result;
      await searchFiles();
      say(`只读索引 ${result.files} 项，更新 ${result.changed} 项。`);
    });
}
async function analyzeFile(file: MediaFile) {
  if (api.local)
    await runAction(async () => {
      analysis.value = await api.local!.analyze(file.id, file.kind);
      analysisTitle.value = file.name;
      if (analysis.value.thumbnailAvailable)
        try {
          thumbnails[file.id] = await api.local!.thumbnail(file.id);
        } catch (error) {
          showError(error);
        }
      await searchFiles();
    });
}
async function viewReport() {
  if (selectedTask.value && api.local)
    await runAction(async () => {
      taskReport.value = await api.local!.report(selectedTask.value!.id);
    });
  else say("演示报告，不包含真实结果。");
}
async function exportReport() {
  if (selectedTask.value && api.local)
    await runAction(async () =>
      say(
        `报告已导出：${await api.local!.exportReport(selectedTask.value!.id, "json")}`,
      ),
    );
}
async function stageSource() {
  if (selectedTask.value && api.local)
    await runAction(async () => {
      await api.local!.confirmSourceRemoval(selectedTask.value!.id);
      say("来源已按确认暂存，仍占来源空间。");
    });
}
async function askAgent() {
  const text = agentInput.value.trim();
  if (!text) return;
  if (api.model) {
    if (localWorkspace.value && !agentCanSend.value) {
      say("本机模式仅使用 Ollama。请先在 Agent 设置中选择已安装的本机模型。");
      return;
    }
    if (!modelConsentRef.value) {
      showError(new Error("本机模型确认界面尚未就绪，请稍后重试"));
      return;
    }
    await modelConsentRef.value.submit(text);
    return;
  }
  agentMessages.value.push({ role: "user", text });
  agentInput.value = "";
  try {
    const reply = await api.agent.ask(text);
    agentMessages.value.push({
      role: "assistant",
      text: reply.text,
      reply,
      actionId: reply.proposedAction?.kind === "open"
        ? reply.proposedAction.actionId
        : isMock && reply.proposedAction ? `act-${Date.now()}` : undefined,
    });
  } catch (error) {
    showError(error);
  }
}

async function refreshAgentModelChoices() {
  const providerConfig = api.modelProviderConfig;
  if (!providerConfig || agentModelBusy.value) return;
  agentModelBusy.value = true;
  try {
    const [config, catalog] = await Promise.all([
      providerConfig.status(),
      api.model?.listLocalModels() ?? Promise.resolve({ available: false, models: [] as string[] }),
    ]);
    agentModelConfig.value = config;
    agentLocalModels.value = catalog.models;
    agentSelectedModel.value = localWorkspace.value && config.provider !== "ollama"
      ? ""
      : agentModelValue.value;
  } catch {
    say("读取模型配置失败，请稍后重试。");
  } finally {
    agentModelBusy.value = false;
  }
}

async function selectAgentModel() {
  const providerConfig = api.modelProviderConfig;
  if (!providerConfig || agentModelBusy.value || !agentSelectedModel.value) return;
  if (localWorkspace.value && agentSelectedModel.value.startsWith("cloud:")) {
    agentSelectedModel.value = "";
    say("本机模式已停用云端模型，请选择本机 Ollama。");
    return;
  }
  agentModelBusy.value = true;
  try {
    if (agentSelectedModel.value.startsWith("cloud:")) {
      agentModelConfig.value = await providerConfig.activateCloudModel();
    } else if (agentSelectedModel.value.startsWith("local:")) {
      const model = agentSelectedModel.value.slice("local:".length);
      if (model) agentModelConfig.value = await providerConfig.selectLocalModel(model);
    }
    agentSelectedModel.value = agentModelValue.value;
  } catch (error) {
    agentSelectedModel.value = agentModelValue.value;
    say(error instanceof Error ? error.message : "切换模型失败，请在 Agent 设置中检查模型配置。");
  } finally {
    agentModelBusy.value = false;
  }
}

watch(page, (next) => {
  if (next === "agent") void refreshAgentModelChoices();
});
watch(localWorkspace, (enabled) => {
  if (!enabled) return;
  // Clear any stale cloud selection immediately when entering offline mode.
  if (agentModelConfig.value?.configured && agentModelConfig.value.provider !== "ollama") {
    agentSelectedModel.value = "";
  }
});

function appendModelReply(value: { query: string; reply: AgentReply }) {
  agentMessages.value.push({ role: "user", text: value.query });
  agentMessages.value.push({
    role: "assistant",
    text: value.reply.text,
    reply: value.reply,
    actionId: value.reply.proposedAction?.kind === "open"
      ? value.reply.proposedAction.actionId
      : undefined,
  });
  if (agentInput.value === value.query) agentInput.value = "";
}
function requestNewAgentConversation() {
  if (modelConsentRef.value?.isBusy()) return;
  if (modelConsentRef.value?.requiresRecovery()) {
    agentNewConversationReview.value = true;
    return;
  }
  startNewAgentConversation();
}
function startNewAgentConversation() {
  agentMessages.value = [];
  agentInput.value = "";
  agentConversationKey.value += 1;
  agentNewConversationReview.value = false;
}
function confirmNewAgentConversation() {
  if (!agentNewConversationReview.value) return;
  agentHasArchivedUnresolvedTurn.value = true;
  startNewAgentConversation();
}
async function confirmAgent(message: AgentMessage, accepted: boolean) {
  if (!message.actionId || agentActionBusy.value) return;
  agentActionBusy.value = true;
  try {
    const result = await api.agent.confirm(message.actionId, accepted);
    if (typeof result === "string") say(result);
    else agentMessages.value.push({
      role: "assistant",
      text: result.text,
      reply: result,
      actionId: result.proposedAction?.kind === "open"
        ? result.proposedAction.actionId
        : undefined,
    });
    message.actionId = undefined;
  } catch (error) {
    // A host action is single use: a failed decision never stays on screen.
    message.actionId = undefined;
    if (message.reply?.proposedAction?.kind === "open") {
      agentMessages.value.push({
        role: "assistant",
        text: "打开未确认：本机没有返回可验证的成功回执，不能把素材标记为已打开。",
      });
    }
    showError(error);
  } finally {
    agentActionBusy.value = false;
  }
}
async function updateSetting<K extends keyof AppSettings>(
  key: K,
  value: AppSettings[K],
) {
  if (localWorkspace.value && (["syncHistory", "syncAgent", "syncThumbnails", "syncPaths", "maxUploadMbps", "webhookEnabled", "webhookUrl"].includes(key) || (key === "aiProvider" && value === "OpenAI 兼容 API"))) {
    say("本机模式已禁用云同步、Webhook 和云端模型设置；不会发送远端请求。");
    return;
  }
  try {
    Object.assign(settings, await api.settings.update({ [key]: value }));
    document.documentElement.dataset.theme = settings.theme;
  } catch (error) {
    showError(error);
  }
}
async function createPreset() {
  presetEditingId.value = null;
  presetDraftName.value = "";
  presetDraftPattern.value = "日期 / 事件 / 设备";
  presetEditorOpen.value = true;
}
function editPreset(preset: NamePreset) {
  presetEditingId.value = preset.id;
  presetDraftName.value = preset.name.trim() === "新建预设" ? "" : preset.name;
  presetDraftPattern.value = preset.pattern;
  presetEditorOpen.value = true;
}
async function savePreset() {
  await runAction(async () => {
    const name = presetDraftName.value.trim();
    const pattern = presetDraftPattern.value.trim();
    if (!name || !pattern) {
      say("请填写预设名称和命名规则。");
      return;
    }
    if (presets.value.some((preset) => preset.id !== presetEditingId.value && preset.name.trim() === name)) {
      say("已有同名预设，请换一个名称以便区分。");
      return;
    }
    const item = await api.presets.save({
      id: presetEditingId.value || crypto.randomUUID(),
      name,
      pattern,
      isDefault: presets.value.find((preset) => preset.id === presetEditingId.value)?.isDefault || false,
    });
    presets.value = presetEditingId.value
      ? presets.value.map((preset) => preset.id === item.id ? item : preset)
      : [...presets.value, item];
    presetEditorOpen.value = false;
    say(presetEditingId.value ? "预设已更新。" : "预设已保存。");
  });
}
function presetDisplayName(preset: NamePreset, index: number) {
  return displayPresetName(preset, index, presets.value);
}
async function removePreset(item: NamePreset) {
  if (item.isDefault) return;
  presetToDelete.value = item;
}
async function confirmRemovePreset() {
  const item = presetToDelete.value;
  if (!item) return;
  await runAction(async () => {
    await api.presets.remove(item.id);
    presets.value = presets.value.filter((preset) => preset.id !== item.id);
    presetToDelete.value = null;
    say("预设已删除。");
  });
}
async function runAudit() {
  await runAction(async () => {
    auditResult.value = await api.audit.run();
    if (api.local) {
      healthyReplicas.value = (await api.local.replicas()).filter(
        (r) => r.verified,
      );
      restorePath.value = healthyReplicas.value[0]?.path || "";
    }
  });
}
async function restoreHealthyReplica() {
  await runAction(async () => {
    if (!restorePath.value) return;
    say(await api.audit.restore(restorePath.value, ""));
  });
}
async function restoreFile(file: MediaFile) {
  if (!isMock) {
    say("来源路径不是副本；请从审计登记的健康副本恢复。");
    return;
  }
  say(await api.audit.restore(file.path, ""));
}
async function createDelivery() {
  await runAction(async () => {
    const item = await api.delivery.create(
      deliveryName.value,
      deliverySelection.value,
      deliveryContact.value,
      deliveryManifest.value,
    );
    say(
      isMock
        ? `演示交付包 ${item.id}`
        : `交付任务 ${item.id} 已登记，请查看实际任务状态。`,
    );
    if (!isMock) {
      await refreshHistory();
      const task = tasks.value.find((t) => t.id === item.id);
      if (task) await openTask(task);
    }
  });
}
async function revokeDevice(device: Device) {
  if (!device.current) {
    await runCloudAction(session.value, localWorkspace.value, async () => {
      await api.account.revoke(device.id);
    });
  }
}
async function clearHistory() {
  await runAction(async () => {
    await api.history.clear();
    tasks.value = [];
    showModal.value = "";
    say("归档历史已清空，媒体文件保留。");
  });
}
async function renameTask(task: ArchiveTask) {
  taskToRename.value = task;
  taskRenameDraft.value = task.name;
}
async function saveTaskRename() {
  const task = taskToRename.value;
  const name = taskRenameDraft.value.trim();
  if (!task || !name) {
    say("记录名称不能为空。");
    return;
  }
  await runAction(async () => {
    await api.history.rename(task.id, name);
    task.name = name;
    taskToRename.value = null;
    say("归档记录名称已更新。");
  });
}
async function connectProvider(provider: "baidu" | "quark") {
  try {
    const allowed = await runCloudAction(session.value, localWorkspace.value, async () => {
      say(await api.settings.connectProvider(provider));
    });
    if (!allowed) say("本机模式已禁用网盘授权；不会发送云端请求。");
  } catch (error) {
    showError(error);
  }
}
async function resetSettings() {
  try {
    Object.assign(settings, await api.settings.reset());
    document.documentElement.dataset.theme = settings.theme;
  } catch (error) {
    showError(error);
  }
}
function setPage(next: Page) {
  if (localWorkspace.value && next === "account") {
    say("账号与设备功能在本机模式下不可用。");
    return;
  }
  page.value = next;
  taskDetail.value = next === "task" ? selectedTask.value : null;
  if (next === "history") void refreshHistory().catch(showError);
  if (next === "library") void searchFiles().catch(showError);
  if (next === "account" && canUseCloud.value)
    void api.account
      .devices()
      .then((value) => (devices.value = value))
      .catch(showError);
  if (next === "presets")
    void api.presets
      .list()
      .then((value) => (presets.value = value))
      .catch(showError);
}
function openBaiduSettings() {
  setPage("settings");
  void nextTick(() => {
    document.getElementById("cloud")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
function toggleGlobalSearch() {
  globalSearchOpen.value = !globalSearchOpen.value;
  globalSearchQuery.value = "";
}
function chooseGlobalSearch(next: Page) {
  setPage(next);
  globalSearchOpen.value = false;
  globalSearchQuery.value = "";
}
function submitGlobalSearch() {
  const match = globalSearchMatches.value[0];
  if (match) chooseGlobalSearch(match.id);
}
function taskStateLabel(state: TaskState) {
  return (
    {
      queued: "排队中",
      running: "进行中",
      paused: "已暂停",
      success: "已完成",
      partial: "部分完成",
      cancelled: "已取消",
      failed: "失败",
    } as Record<TaskState, string>
  )[state];
}
function baiduTaskStateLabel(state: string) {
  return ({
    queued: "等待上传",
    uploading: "上传中",
    failed: "上传失败",
    completed: "已上传并核验",
    cancelled: "已取消",
    committing: "正在核对",
    commit_uncertain: "结果待核对",
  } as Record<string, string>)[state] || "状态未知";
}
</script>

<template>
  <TrayAgentPanel v-if="isTrayAgentWindow" />
  <main
    v-else-if="!session && !localWorkspace"
    class="login-shell"
    :inert="!!pendingConfirmation || !!analysis || !!taskReport"
  >
    <div class="login-brand">
      <img class="brand-mark-img" src="/yingxu-app-icon.png" alt="影序应用图标" />
      <span>影序 <small>YINGXU</small></span>
    </div>
    <section class="login-card">
      <div class="login-intro">
        <span class="eyebrow">影序 · 桌面端 V2 草稿</span>
        <h1>欢迎回到影序</h1>
        <p>
          登录后同步备份进度、归档记录与设备状态。你的照片和视频原片不会上传到影序云端。
        </p>
      </div>
      <div class="login-tabs">
        <button :class="{ chosen: loginMode === 'email-password' }" @click="loginMode = 'email-password'">邮箱密码</button>
        <button :class="{ chosen: loginMode === 'email-code' }" @click="loginMode = 'email-code'">邮箱验证码</button>
      </div>
      <div class="login-fields">
        <template v-if="loginMode === 'email-password' || loginMode === 'email-code'">
          <label>邮箱或用户名</label>
          <input v-model="loginIdentifier" autocomplete="username" placeholder="请输入邮箱地址或用户名" />
          <label>{{ loginMode === 'email-password' ? '密码' : '邮箱验证码' }}</label>
          <div class="proof-field">
            <input v-model="loginProof" :type="loginMode === 'email-password' ? 'password' : 'text'" :autocomplete="loginMode === 'email-password' ? 'current-password' : 'one-time-code'" :placeholder="loginMode === 'email-password' ? '请输入密码' : '请输入邮箱验证码'" />
            <button v-if="loginMode === 'email-code'" class="text-button" @click="loginError = '邮箱验证码服务尚未接入；当前不会发送邮件。'">获取验证码</button>
          </div>
          <div class="form-foot">
            <button class="text-button" @click="showPasswordReset">忘记密码？</button>
            <button class="text-button" @click="showRegistration">注册账号</button>
          </div>
        </template>
        <template v-else>
          <div class="auth-view-heading">
            <button class="text-button" @click="loginMode = 'email-password'; loginError = ''">‹ 返回登录</button>
            <b>{{ loginMode === 'register' ? '创建影序账号' : '重置密码' }}</b>
            <span>{{ loginMode === 'register' ? '验证邮箱并设置登录密码。' : '我们会向已注册邮箱发送验证码。' }}</span>
          </div>
          <label>邮箱</label>
          <input v-model="loginIdentifier" autocomplete="email" placeholder="请输入邮箱地址" />
          <template v-if="loginMode === 'register'">
            <label>昵称</label>
            <input v-model="loginNickname" autocomplete="nickname" maxlength="40" placeholder="请输入你希望显示的昵称" />
          </template>
          <label>邮箱验证码</label>
          <div class="proof-field">
            <input v-model="loginProof" inputmode="numeric" autocomplete="one-time-code" placeholder="请输入邮箱验证码" />
            <button class="text-button" @click="loginError = '邮箱验证服务尚未接入；当前不会发送邮件。'">获取验证码</button>
          </div>
          <label>{{ loginMode === 'register' ? '设置密码' : '新密码' }}</label>
          <input type="password" autocomplete="new-password" placeholder="至少 8 位" />
          <label>确认密码</label>
          <input type="password" autocomplete="new-password" placeholder="请再次输入密码" />
        </template>
      </div>
      <p v-if="loginError" class="error-note">{{ loginError }}</p>
      <p v-if="props.testTransport" class="integration-banner">
        {{ props.testTransport }}
      </p>
      <p v-if="!isMock" class="auth-not-connected">真实认证接口尚未连接；不会伪造登录、发送验证码或创建账号。</p>
      <button
        class="primary-button login-submit"
        @click="submitAuthView"
        :disabled="loginBusy || (!isMock && (loginMode === 'register' || loginMode === 'forgot-password'))"
      >
        {{
          loginBusy ? "正在登录…" :
          loginMode === 'register' ? '创建账号' :
          loginMode === 'forgot-password' ? '重置密码' :
          isMock ? '登录影序（演示）' : '登录影序账号'
        }}
      </button>
      <p v-if="canEnterLocalWorkspace" class="local-mode-note">
        本机模式无需登录，照片、任务和 Agent 留在此设备；账号、云同步与网盘功能将停用。
      </p>
      <button
        v-if="canEnterLocalWorkspace"
        class="secondary-button full-width local-entry"
        @click="enterLocalWorkspace"
        :disabled="operationBusy"
      >
        进入本机模式（不登录、不云同步）
      </button>
      <p class="login-disclaimer">
        {{ isMock ? "浏览器预览 · 登录仅为界面演示" : hostStatus }}
      </p>
    </section>
    <footer class="login-footer">
      <span><LockKeyhole :size="14" /> 安全同步 · 原片留在你的设备</span
      ><span>Windows / macOS</span>
    </footer>
    <div v-if="notice" class="toast" role="status" aria-live="polite">
      {{ notice }}
    </div>
  </main>

  <div
    v-else
    class="app-shell"
    :data-api-mode="api.mode"
    :inert="!!pendingConfirmation || !!analysis || !!taskReport"
  >
    <aside class="sidebar">
      <div class="brand-row">
        <img class="brand-mark-img" src="/yingxu-app-icon.png" alt="影序应用图标" />
        <div>
          <b>影序 <small>YINGXU</small></b
          ><span>智能影像管理</span>
        </div>
      </div>
      <button class="workspace-switch">
        <span class="workspace-avatar">{{ displayName.slice(0, 1) }}</span
        ><span
          ><b>{{ displayName }} 的空间</b
          ><small>{{
            isMock ? "本机设备 · Mac Studio" : "本地设备 · 云端未登录"
          }}</small></span
        ><ChevronDown :size="14" />
      </button>
      <nav class="side-nav">
        <section v-for="group in navGroups" :key="group.title">
          <h2>{{ group.title }}</h2>
          <button
            v-for="item in group.items"
            :key="item.id"
            :class="['nav-item', { active: page === item.id }]"
            :aria-label="item.label"
            :title="item.label"
            @click="setPage(item.id)"
          >
            <component :is="item.icon" :size="17" /><span>{{ item.label }}</span
            ><i
              v-if="
                item.id === 'backup' && tasks.some((t) => t.state === 'running')
              "
              class="nav-dot"
            ></i>
          </button>
        </section>
      </nav>
      <div class="side-bottom">
        <button
          class="nav-item"
          :class="{ active: page === 'account' }"
          aria-label="账号与设备"
          :title="localWorkspace ? '本机模式下不可用' : '账号与设备'"
          :disabled="localWorkspace"
          @click="setPage('account')"
        >
          <LockKeyhole :size="17" /><span>{{ localWorkspace ? '账号与设备（已停用）' : '账号与设备' }}</span></button
        ><button
          class="nav-item"
          :class="{ active: page === 'settings' }"
          aria-label="偏好设置"
          title="偏好设置"
          @click="setPage('settings')"
        >
          <Settings2 :size="17" /><span>偏好设置</span>
        </button>
        <div class="account-chip">
          <span class="user-avatar">{{ displayName.slice(0, 1) }}</span
          ><span
            ><b>{{ displayName }}</b
            ><small>{{ displayEmail }}</small></span
          ><button class="icon-button" :title="localWorkspace ? '退出本机模式' : '退出登录'" @click="logout">
            <MoreHorizontal :size="16" />
          </button>
        </div>
      </div>
    </aside>
    <section class="main-area">
      <header class="topbar">
        <div class="breadcrumbs">
          <span>影序</span><ChevronRight :size="13" /><b>{{ activeTitle }}</b>
        </div>
        <div class="top-actions">
          <span v-if="!localWorkspace" class="draft-chip"><i></i> V2 草稿预览</span
          ><span class="demo-chip"
            ><i></i>{{ isMock ? "演示数据" : localWorkspace ? "本机模式 · 云端已停用" : "本地真实数据" }}</span
          >
          <div class="search-action-wrap">
            <button
              class="icon-button"
              aria-label="搜索工作区和工具"
              :aria-expanded="globalSearchOpen"
              @click="toggleGlobalSearch"
            >
              <Search :size="17" /></button
            ><span
              v-if="!globalSearchOpen"
              class="search-tooltip"
              role="tooltip"
              >搜索</span
            >
            <div
              v-if="globalSearchOpen"
              class="global-search-popover"
              @keydown.esc="globalSearchOpen = false"
            >
              <form
                class="global-search-field"
                @submit.prevent="submitGlobalSearch"
              >
                <Search :size="16" /><input
                  v-model="globalSearchQuery"
                  autofocus
                  placeholder="搜索工作区和工具"
                  aria-label="搜索工作区和工具"
                /><button
                  v-if="globalSearchQuery"
                  type="button"
                  class="global-search-clear"
                  aria-label="清除搜索"
                  @click="globalSearchQuery = ''"
                >
                  ×
                </button>
              </form>
              <div
                v-if="globalSearchQuery.trim() && globalSearchMatches.length"
                class="global-search-results"
              >
                <button
                  v-for="item in globalSearchMatches"
                  :key="item.id"
                  class="global-search-result"
                  @click="chooseGlobalSearch(item.id)"
                >
                  <component :is="item.icon" :size="15" /><span>{{
                    item.label
                  }}</span
                  ><small>{{ item.group }}</small>
                </button>
              </div>
              <div
                v-else-if="globalSearchQuery.trim()"
                class="global-search-empty"
              >
                未搜索到相关页面
              </div>
              <div v-else class="global-search-hint">
                搜索工作区与工具，输入完整名称可直接跳转
              </div>
            </div>
          </div>
          <button
            class="icon-button"
            aria-label="通知"
            title="通知"
            @click="say('当前没有新的演示通知。')"
          >
            <Bell :size="17" /></button
          ><span class="top-divider"></span
          ><button
            class="theme-button"
            @click="
              updateSetting(
                'theme',
                settings.theme === 'light' ? 'dark' : 'light',
              )
            "
          >
            <component
              :is="settings.theme === 'light' ? Sun : Moon"
              :size="15"
            />{{ settings.theme === "light" ? "浅色" : "深色" }}
          </button>
        </div>
      </header>
      <div class="page-scroll">
        <div class="page-content">
          <div v-if="notice" class="toast inline-toast">{{ notice }}</div>
          <div v-if="localWorkspace" class="local-mode-banner" role="status">
            <b>本机模式已开启</b><span>无需账号；本地 Agent、素材索引和备份仅在此设备运行。云账号、跨设备同步与网盘入口均已停用。</span>
          </div>
          <ScanSummary :report="page === 'backup' ? backupScan : page === 'transfer' ? transferScan : page === 'library' ? indexScan : null" />

          <template v-if="page === 'home' && !isMock"
            ><PageHeading
              :eyebrow="localWorkspace ? '本机模式 · 未登录、不云同步' : '本地联调工作区 · 云端未登录'"
              title="工作台"
              :description="props.testTransport || hostStatus"
            />
            <section class="panel native-home-summary">
              <div class="native-home-cluster">
                <div class="native-overview-heading">
                  <div>
                    <h3>本地资料概况</h3>
                    <p>索引和任务数据保存在这台电脑</p>
                  </div>
                  <span :class="['state-pill', hostStatus.includes('已连接') ? 'success' : 'queued']"><i :class="['status-dot', hostStatus.includes('已连接') ? 'online' : 'running']"></i> {{ hostStatus.includes('已连接') ? '本地服务已连接' : hostStatus }}</span>
                </div>
                <div class="native-metrics">
                  <span><b>{{ summary.fileCount.toLocaleString() }}</b><small>已索引文件</small></span>
                  <span><b>{{ summary.totalSize || '—' }}</b><small>资料库容量</small></span>
                  <span><b>{{ summary.reviewCount.toLocaleString() }}</b><small>待复核素材</small></span>
                </div>
                <div class="native-home-actions">
                  <button class="primary-button" @click="setPage('backup')">开始本地备份</button>
                  <button class="secondary-button" @click="setPage('library')">打开资料库</button>
                  <button class="secondary-button" @click="setPage('history')">查看归档记录</button>
                </div>
              </div>
            </section>
            <section class="content-grid native-home-lower">
              <div class="panel">
                <div class="panel-heading">
                  <div>
                    <h3>最近本地任务</h3>
                    <p>查看任务进度、结果和逐个目的地状态</p>
                  </div>
                  <button class="text-button" @click="setPage('history')">全部记录 <ArrowRight :size="14" /></button>
                </div>
                <div v-if="tasks.length" class="native-task-list">
              <button
                v-for="task in tasks.slice(0, 4)"
                :key="task.id"
                class="native-task"
                @click="openTask(task)"
              >
                    <span class="task-icon"><Archive :size="16" /></span>
                    <span class="native-task-main">
                      <b>{{ task.name }}</b>
                      <small>{{ task.createdAt }} · {{ task.files }} 个文件 · {{ task.size }}</small>
                      <span class="native-task-track"><i :style="{ width: `${Math.max(0, Math.min(100, task.progress))}%` }"></i></span>
                    </span>
                    <span class="native-task-status">
                      <span :class="['state-pill', task.state]">{{ taskStateLabel(task.state) }}</span>
                      <small>{{ task.progress }}%</small>
                    </span>
              </button>
                </div>
                <div v-else class="native-home-empty">
                  <span class="task-icon"><Archive :size="16" /></span>
                  <span><b>尚无本地任务</b><small>选择“开始本地备份”创建第一项任务</small></span>
                  <button class="text-button" @click="setPage('backup')">开始备份 <ArrowRight :size="13" /></button>
                </div>
              </div>
              <div class="panel quick-panel native-home-quick">
                <div class="panel-heading native-baidu-heading">
                  <div><h3>百度网盘</h3><p>授权状态与上传任务</p></div>
                  <span :class="['state-pill', dashboardBaiduStatus.tone]">{{ dashboardBaiduStatus.label }}</span>
                </div>
                <p class="native-baidu-detail">{{ dashboardBaiduStatus.detail }}</p>
                <div class="native-baidu-stats" aria-label="百度网盘上传统计">
                  <div><b>{{ dashboardBaiduProvider?.authorized && canUseCloud ? dashboardBaiduCounts.queued : "—" }}</b><small>等待上传</small></div>
                  <div><b>{{ dashboardBaiduProvider?.authorized && canUseCloud ? dashboardBaiduCounts.uploading : "—" }}</b><small>上传中</small></div>
                  <div><b>{{ dashboardBaiduProvider?.authorized && canUseCloud ? dashboardBaiduCounts.verified : "—" }}</b><small>已核验</small></div>
                </div>
                <div class="native-baidu-latest">
                  <b>最近上传</b>
                  <span v-if="latestDashboardBaiduTask">
                    <span>{{ latestDashboardBaiduTask.name }}</span>
                    <small>{{ baiduTaskStateLabel(latestDashboardBaiduTask.state) }} · {{ latestDashboardBaiduTask.chunksTotal ? Math.round(latestDashboardBaiduTask.chunksDone / latestDashboardBaiduTask.chunksTotal * 100) : 0 }}%</small>
                  </span>
                  <small v-else>{{ dashboardBaiduProvider?.authorized && canUseCloud ? "暂无上传记录" : "连接后显示上传记录" }}</small>
                </div>
                <div class="native-baidu-actions">
                  <button class="secondary-button compact" @click="openBaiduSettings">管理网盘</button>
                  <button class="primary-button compact" :disabled="!api.netdisk || !canUseCloud || !dashboardBaiduProvider?.authorized || !dashboardBaiduProvider.realUploadAvailable" @click="setPage('library')">选择素材上传</button>
                </div>
              </div>
            </section>
            <div class="notice-card native-home-safety">
              <ShieldCheck :size="17" />
              <div><b>本地安全状态</b><span>原始素材保留在来源设备；备份副本经校验后记录到本机数据库。</span></div>
            </div></template
          ><template v-else-if="page === 'home'">
            <header class="welcome-row">
              <div>
                <span class="eyebrow">桌面工作空间 · 演示模式</span>
                <h1>早上好，{{ displayName }} <span>☀</span></h1>
                <p>今天也为你的每一份作品，留好安全副本。</p>
              </div>
              <button
                class="primary-button"
                @click="
                  backupStep = 1;
                  setPage('backup');
                "
              >
                <Plus :size="16" /> 新建备份任务
              </button>
            </header>
            <section class="hero-card">
              <div>
                <span class="hero-eyebrow"
                  ><i class="status-dot running"></i>
                  {{ tasks[0]?.phase || "任务状态" }} ·
                  {{ tasks[0]?.progress || 0 }}%</span
                >
                <h2>{{ tasks[0]?.name || "欢迎开始你的第一次归档" }}</h2>
                <p>
                  原片保留在来源设备；任务进度、校验状态和目标结果可随时查看。
                </p>
                <div class="progress-line">
                  <i :style="{ width: `${tasks[0]?.progress || 0}%` }"></i>
                </div>
                <div class="hero-meta">
                  <span>{{ tasks[0]?.source || "尚无来源" }}</span
                  ><b>{{ tasks[0]?.speed || "等待任务" }}</b>
                </div>
              </div>
              <span class="hero-art"
                ><Image :size="42" /><ShieldCheck :size="22" /></span
              ><button
                class="hero-link"
                @click="tasks[0] && openTask(tasks[0])"
              >
                <ArrowRight :size="17" />
              </button>
            </section>
            <section class="stat-grid">
              <article class="stat-card">
                <span class="stat-icon green"><Database :size="17" /></span
                ><small>已归档文件</small
                ><b>{{ summary.fileCount.toLocaleString() }} <em>个</em></b
                ><span>{{ summary.totalSize }} 安全副本</span>
              </article>
              <article class="stat-card">
                <span class="stat-icon gold"><Activity :size="17" /></span
                ><small>近 30 天任务</small><b>{{ tasks.length }} <em>个</em></b
                ><span>包含部分完成与失败记录</span>
              </article>
              <article class="stat-card">
                <span class="stat-icon blue"><Cloud :size="17" /></span
                ><small>同步目的地</small
                ><b>{{ summary.destinations }} <em>个</em></b
                ><span>网盘状态为演示数据</span>
              </article>
              <article class="stat-card">
                <span class="stat-icon violet"><Sparkles :size="17" /></span
                ><small>质量待复核</small
                ><b>{{ summary.reviewCount }} <em>项</em></b
                ><span>不会自动删除原片</span>
              </article>
            </section>
            <section class="content-grid">
              <div class="panel">
                <div class="panel-heading">
                  <div>
                    <h3>最近归档</h3>
                    <p>查看任务状态与逐个目的地结果</p>
                  </div>
                  <button class="text-button" @click="setPage('history')">
                    全部记录 <ArrowRight :size="14" />
                  </button>
                </div>
                <div class="task-list">
                  <button
                    v-for="task in tasks.slice(0, 4)"
                    :key="task.id"
                    class="task-row"
                    @click="openTask(task)"
                  >
                    <span class="task-icon"><Archive :size="17" /></span
                    ><span class="task-name"
                      ><b>{{ task.name }}</b
                      ><small
                        >{{ task.createdAt }} · {{ task.files }} 个 ·
                        {{ task.size }}</small
                      ></span
                    ><span class="task-dests"
                      ><i
                        v-for="dest in task.destinations"
                        :key="dest.id"
                        :class="['mini-state', dest.state]"
                        >{{ dest.name }} · {{ taskStateLabel(dest.state) }}</i
                      ></span
                    ><span :class="['state-pill', task.state]">{{
                      taskStateLabel(task.state)
                    }}</span
                    ><ChevronRight :size="15" />
                  </button>
                </div>
              </div>
              <div class="panel quick-panel">
                <div class="panel-heading">
                  <div>
                    <h3>继续处理</h3>
                    <p>常用影像工作流</p>
                  </div>
                  <WandSparkles :size="17" />
                </div>
                <button class="quick-action" @click="setPage('transfer')">
                  <Folder :size="17" /><span
                    ><b>转移老内容</b><small>校验后确认移除来源</small></span
                  ><ArrowRight :size="14" /></button
                ><button class="quick-action" @click="setPage('health')">
                  <ShieldCheck :size="17" /><span
                    ><b>备份健康审计</b><small>检查副本完整性</small></span
                  ><ArrowRight :size="14" /></button
                ><button class="quick-action" @click="setPage('agent')">
                  <Sparkles :size="17" /><span
                    ><b>询问 Agent</b><small>检索素材与任务</small></span
                  ><ArrowRight :size="14" />
                </button>
              </div>
            </section>
            <section class="panel notice-card">
              <ShieldCheck :size="18" />
              <div>
                <b>安全提醒</b
                ><span
                  >影序演示不会读写本机文件。真实备份、云盘授权与账号同步将在前端确认后分阶段接入。</span
                >
              </div>
              <button class="text-button" @click="setPage('settings')">
                查看隐私设置 <ArrowRight :size="13" />
              </button>
            </section>
          </template>

          <template v-else-if="page === 'backup'">
            <PageHeading
              eyebrow="安全导入"
              title="复制新内容"
              description="检测来源、预检目的地并预览命名；只有你确认后才开始备份。"
            />
            <p v-if="!isMock" class="integration-banner">
              真实文件模式：扫描只读，后端预检后再次确认才写入。
            </p>
            <div class="stepper">
              <button
                v-for="(label, index) in [
                  '来源扫描',
                  '备份目的地',
                  '命名预览',
                  '确认摘要',
                ]"
                :key="label"
                :class="{
                  current: backupStep === index + 1,
                  done: backupStep > index + 1,
                }"
                :disabled="!canEnterStep(index + 1, wizardContext)"
                @click="backupStep = index + 1"
              >
                <i>{{ backupStep > index + 1 ? "✓" : index + 1 }}</i
                >{{ label }}
              </button>
            </div>
            <section class="panel flow-panel">
              <template v-if="backupStep === 1"
                ><div class="panel-heading">
                  <div>
                    <h3>选择来源设备</h3>
                  </div>
                  <button
                    class="secondary-button"
                    @click="scanSource"
                    :disabled="scanBusy"
                  >
                    {{ scanBusy ? "扫描中…" : "重新扫描来源" }}
                  </button>
                </div>
                <div v-if="sources.length" class="source-card">
                  <HardDrive :size="22" />
                  <div>
                    <b>{{ sources[0].name }} · 已连接</b
                    ><span
                      >{{ sources[0].path }} ·
                      {{ sources[0].files.toLocaleString() }} 个文件 ·
                      {{ sources[0].size }}</span
                    >
                  </div>
                  <CheckCircle2 :size="18" />
                </div>
                <div v-else class="empty-state">
                  <HardDrive :size="28" /><b>尚未扫描来源</b
                  ><span>插入存储卡或移动盘后手动开始预检。</span
                  ><button
                    class="primary-button"
                    @click="scanSource"
                    :disabled="scanBusy"
                  >
                    {{
                      scanBusy
                        ? "扫描中…"
                        : isMock
                          ? "开始扫描（演示）"
                          : "选择来源并扫描"
                    }}
                  </button>
                </div>
                <div class="scan-note">
                  <ShieldCheck :size="16" />{{
                    isMock
                      ? "扫描使用演示数据。"
                      : sources.length
                        ? "来源已通过原生目录选择器授权，扫描不开始写入。"
                        : "请先通过原生目录选择器选择来源；扫描不开始写入。"
                  }}
                </div></template
              >
              <template v-else-if="backupStep === 2"
                ><div class="panel-heading">
                  <div>
                    <h3>选择备份目的地</h3>
                    <p>
                      备份目标为本地磁盘；百度上传在资料库单独排队。不支持 NAS/局域网目录。
                    </p>
                  </div>
                </div>
                <div v-if="!isMock" class="option-list">
                  <button
                    class="secondary-button"
                    @click="chooseBackupDestination"
                  >
                    选择本地备份目录</button
                  ><label
                    v-for="target in backupTargets"
                    :key="target"
                    class="option-row"
                    ><input
                      type="checkbox"
                      checked
                      @change="
                        backupTargets = backupTargets.filter(
                          (t) => t !== target,
                        )
                      "
                    /><span>{{ target }}</span></label
                  >
                </div>
                <div v-else class="option-list">
                  <label
                    v-for="item in [
                      {
                        id: 'local',
                        name: '本机硬盘',
                        meta: 'Mac Studio / 摄影归档 · 可用 616 GB',
                      },
                      {
                        id: 'baidu',
                        name: '百度网盘',
                        meta: '需要官方账号授权',
                      },
                      {
                        id: 'quark',
                        name: '夸克网盘',
                        meta: '官方 API 能力待核验',
                      },
                    ]"
                    :key="item.id"
                    class="option-row"
                    ><input
                      v-model="backupTargets"
                      type="checkbox"
                      :value="item.id"
                      :disabled="item.id !== 'local'"
                    /><span
                      ><b>{{ item.name }}</b
                      ><small>{{ item.meta }}</small></span
                    ><span class="demo-label">{{
                      item.id === "local" ? "可用" : "待接入"
                    }}</span></label
                  >
                </div>
                <div class="scan-note">
                  {{
                    isMock
                      ? "当前为演示目标。"
                      : "实际空闲空间在预检确认时读取；网盘上传使用独立队列，不能作为本地路径。"
                  }}
                </div></template
              >
              <template v-else-if="backupStep === 3"
                ><div class="panel-heading">
                  <div>
                    <h3>命名规则与目录预览</h3>
                    <p>可应用导入预设，并在执行前检查层级名称。</p>
                  </div>
                  <button class="text-button" @click="setPage('presets')">
                    管理预设 <ArrowRight :size="13" />
                  </button>
                </div>
                <label class="field-label">导入预设</label
                ><select v-model="backupPreset" class="select-field">
                  <option
                    v-for="(preset, index) in presets"
                    :key="preset.id"
                    :value="preset.id"
                  >
                    {{ presetDisplayName(preset, index) }} · {{ preset.pattern }}
                  </option>
                </select>
                <div v-if="!isMock" class="two-column-fields">
                  <label
                    v-for="(label, key) in {
                      date: '日期',
                      event: '事件',
                      device: '设备目录名称',
                      location: '地点目录名称',
                      kind: '类型',
                      custom: '自定义文本',
                    }"
                    :key="key"
                    ><span>{{ label }}</span
                    ><input v-model="namingValues[key]"
                  /></label>
                </div>
                <div v-else class="rule-chips">
                  <span>拍摄日期</span><ChevronRight :size="13" /><span
                    >事件名称</span
                  ><ChevronRight :size="13" /><span>设备型号</span
                  ><button
                    class="secondary-button compact"
                    @click="
                      say(
                        '规则编辑表单已在 V2 预览中留位，复杂规则由本机任务接口生成。',
                      )
                    "
                  >
                    <Plus :size="13" />编辑规则
                  </button>
                </div>
                <div class="path-preview">
                  <Folder :size="15" />{{
                    isMock
                      ? "摄影归档 / 2025 / 04-21_海边日落 / Sony_A7_IV"
                      : "最终命名目录将在后端预检确认框显示"
                  }}
                </div>
                <label class="check-row"
                  ><input
                    v-model="settings.verify"
                    type="checkbox"
                  />完成复制后执行 SHA-256 完整性校验</label
                ></template
              >
              <template v-else
                ><div class="panel-heading">
                  <div>
                    <h3>确认备份任务</h3>
                    <p>
                      {{
                        isMock
                          ? "确认后启动演示任务。"
                          : "下一步展示后端实际预检结果，再次确认后开始真实写入。"
                      }}
                    </p>
                  </div>
                  <span class="state-pill queued">等待确认</span>
                </div>
                <dl class="summary-list">
                  <div>
                    <dt>来源</dt>
                    <dd>{{ sources[0]?.path || "SD_CARD/DCIM（示例）" }}</dd>
                  </div>
                  <div>
                    <dt>备份目标</dt>
                    <dd>
                      {{
                        isMock
                          ? backupTargets
                              .map((id) =>
                                id === "local"
                                  ? "本机硬盘"
                                  : id === "baidu"
                                    ? "百度网盘"
                                    : "夸克网盘",
                              )
                              .join("、")
                          : backupTargets.join("、") || "请选择目标"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>命名预设</dt>
                    <dd>
                      {{
                        presets.find((p) => p.id === backupPreset)?.name ||
                        "默认规则"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>文件与校验</dt>
                    <dd>
                      {{ sources[0]?.files || 0 }} 个 ·
                      {{ sources[0]?.size || "—" }} ·
                      {{ settings.verify ? "SHA-256" : "按设置跳过校验" }}
                    </dd>
                  </div>
                </dl>
                <div class="consent-box">
                  <ShieldCheck :size="18" /><span
                    >普通备份只复制；同名不覆盖。网盘上传必须等本地副本校验后执行。</span
                  >
                </div></template
              >
              <div class="panel-actions">
                <button
                  class="secondary-button"
                  v-if="backupStep > 1"
                  @click="backupStep--"
                >
                  上一步</button
                ><span v-else></span
                ><button
                  v-if="backupStep < 4"
                  class="primary-button"
                  @click="
                    backupStep === 1 && !sources.length
                      ? scanSource()
                      : backupStep++
                  "
                >
                  {{
                    backupStep === 1 && !sources.length ? "先扫描来源" : "继续"
                  }}
                  <ArrowRight :size="15" /></button
                ><button
                  v-else
                  class="primary-button"
                  @click="startBackup"
                  :disabled="!backupTargets.length || operationBusy"
                >
                  <Play :size="15" />{{
                    isMock ? "确认并开始（演示）" : "预检并查看确认摘要"
                  }}
                </button>
              </div>
            </section>
          </template>

          <template v-else-if="page === 'transfer'">
            <PageHeading
              eyebrow="安全迁移"
              title="转移老内容"
              description="来源先只读扫描；目标副本通过校验并经你再次确认后，才会移除来源。"
            />
            <section class="panel flow-panel">
              <div class="two-column-fields transfer-path-fields">
                <label
                  ><span>来源目录</span
                  ><input
                    :value="
                      isMock
                        ? '/Volumes/旧硬盘/待整理'
                        : transferFiles[0]?.path || '扫描时选择真实来源'
                    "
                    readonly /></label
                ><label
                  ><span>目标目录</span
                  ><input v-model="transferTarget" :readonly="!isMock" /></label
                ><button
                  v-if="!isMock"
                  class="secondary-button transfer-destination-button"
                  @click="chooseTransferDestination"
                >
                  选择目标目录
                </button>
              </div>
              <div class="panel-heading">
                <div>
                  <h3>转移候选</h3>
                </div>
                <button
                  class="secondary-button"
                  @click="inspectTransfer"
                  :disabled="transferBusy"
                >
                  {{
                    transferBusy
                      ? "只读扫描中…"
                      : isMock
                        ? "扫描并分析（演示）"
                        : "选择来源并只读索引"
                  }}
                </button>
              </div>
              <div v-if="transferFiles.length" class="file-table">
                <div class="table-head">
                  <span>选择</span><span>文件</span><span>类型 / 大小</span
                  ><span>质量与重复提示</span><span>来源处理</span>
                </div>
                <label
                  v-for="file in transferFiles"
                  :key="file.id"
                  class="table-row"
                  ><input
                    v-model="selectedTransfer"
                    type="checkbox"
                    :value="file.id"
                  /><span
                    ><b>{{ file.name }}</b
                    ><small>{{ file.path }}</small></span
                  ><span>{{ file.kind }} · {{ file.size }}</span
                  ><span>{{ file.quality }}</span
                  ><span>校验后待确认</span></label
                >
              </div>
              <div v-else class="empty-state compact-empty">
                <Folder :size="25" /><b>先扫描来源</b>
              </div>
              <label class="check-row danger-check"
                ><input
                  v-model="transferConfirmSource"
                  type="checkbox"
                />我确认：仅在目标副本 SHA-256
                校验成功且来源未变化后，移除已转移来源文件</label
              >
              <div class="panel-actions">
                <button
                  class="primary-button"
                  @click="beginTransfer"
                  :disabled="!selectedTransfer.length"
                >
                  <ArrowRight :size="15" />预览并启动转移（演示）
                </button>
              </div>
            </section>
          </template>

          <template v-else-if="page === 'library'">
            <PageHeading
              :eyebrow="isMock ? '本机素材 · 演示索引' : '本机素材 · 只读索引'"
              title="影像资料库"
              description="按名称、日期、设备、地点、类型和质量标签检索素材。"
              ><button
                v-if="!isMock"
                class="primary-button"
                @click="indexLibrary"
                :disabled="operationBusy"
              >
                选择来源并只读索引
              </button></PageHeading
            >
            <section class="panel">
              <div class="filter-toolbar">
                <div class="search-field">
                  <Search :size="16" /><input
                    v-model="libraryQuery"
                    placeholder="搜索文件名、设备、地点或路径"
                    @keydown.enter="searchFiles"
                  /><button class="text-button" @click="searchFiles">
                    搜索
                  </button>
                </div>
                <select
                  v-model="filterKind"
                  class="select-field small-select"
                  @change="searchFiles"
                >
                  <option value="all">全部类型</option>
                  <option value="photo">照片</option>
                  <option value="video">视频</option>
                  <option value="other">关联文件</option></select
                ><select
                  v-model="filterQuality"
                  class="select-field small-select"
                  @change="searchFiles"
                >
                  <option value="all">全部质量</option>
                  <option value="good">正常</option>
                  <option value="review">待复核</option>
                  <option value="unanalyzed">未分析</option>
                </select>
              </div>
              <div class="media-grid">
                <article
                  v-for="file in visibleFiles"
                  :key="file.id"
                  class="media-card"
                >
                  <div :class="['media-art', file.kind, file.qualityState]">
                    <img
                      v-if="thumbnails[file.id]"
                      :src="thumbnails[file.id]"
                      :alt="file.name"
                    /><Image
                      v-else-if="file.kind === 'photo'"
                      :size="25"
                    /><FileVideo
                      v-else-if="file.kind === 'video'"
                      :size="25"
                    /><Copy v-else :size="25" /><span>{{
                      file.kind === "photo"
                        ? "PHOTO"
                        : file.kind === "video"
                          ? "VIDEO"
                          : "SIDECAR"
                    }}</span>
                  </div>
                  <div class="media-copy">
                    <b>{{ file.name }}</b
                    ><small>{{ file.capturedAt }} · {{ file.device }}</small
                    ><small>{{ file.location }} · {{ file.size }}</small>
                    <div class="quality-row">
                      <span :class="file.qualityState">{{ file.quality }}</span
                      ><button
                        v-if="!isMock && file.kind !== 'other'"
                        class="text-button"
                        @click="analyzeFile(file)"
                        :disabled="operationBusy || !localCapabilities"
                      >
                        {{
                          file.kind === "video" ? "分析视频" : "分析 / 预览"
                        }}</button
                      ><button v-if="api.netdisk && !localWorkspace" class="text-button" @click="queueBaidu(file)">加入百度上传队列</button><button
                        v-if="isMock"
                        class="text-button"
                        @click="restoreFile(file)"
                      >
                        恢复副本
                      </button>
                    </div>
                  </div>
                </article>
                <div
                  v-if="libraryPage.status === 'empty'"
                  class="empty-state library-empty-state"
                >
                  没有匹配素材，请调整搜索或筛选条件。
                </div>
                <p
                  v-else-if="libraryPage.status === 'loading'"
                  class="empty-state"
                >
                  正在读取资料库…
                </p>
              </div>
              <div class="library-pager">
                <span class="pager-summary">{{
                  describeLibraryPage(libraryPage)
                }}</span>
                <button
                  class="text-button"
                  :disabled="!canLoadPreviousPage(libraryPage)"
                  @click="loadPreviousPage"
                >
                  上一页
                </button>
                <button
                  class="text-button"
                  :disabled="!canLoadNextPage(libraryPage)"
                  @click="loadNextPage"
                >
                  下一页
                </button>
              </div>
              <p
                v-if="libraryPage.status === 'error'"
                class="library-error"
                role="alert"
              >
                {{ libraryPage.error }}
              </p>
            </section>
          </template>

          <template v-else-if="page === 'history'">
            <PageHeading
              eyebrow="可追溯记录"
              title="归档记录"
              description="查看任务状态、逐个目标结果、失败原因和恢复步骤。"
            />
            <section class="panel">
              <div class="filter-toolbar">
                <div class="search-field">
                  <Search :size="16" /><input
                    v-model="historyQuery"
                    placeholder="搜索任务名称或任务 ID"
                    @keydown.enter="refreshHistory"
                  /><button class="text-button" @click="refreshHistory">
                    搜索
                  </button>
                </div>
                <select
                  v-model="filterState"
                  class="select-field small-select"
                  @change="refreshHistory"
                >
                  <option value="all">全部状态</option>
                  <option value="success">成功</option>
                  <option value="partial">部分完成</option>
                  <option value="running">进行中</option>
                  <option value="paused">暂停</option>
                  <option value="cancelled">已取消</option>
                  <option value="failed">失败</option></select
                ><button
                  class="secondary-button"
                  @click="showModal = 'clear-history'"
                >
                  清空历史
                </button>
              </div>
              <div class="history-list">
                <article
                  v-for="task in tasks"
                  :key="task.id"
                  class="history-row"
                >
                  <div class="task-icon"><Archive :size="17" /></div>
                  <div class="task-name">
                    <b>{{ task.name }}</b
                    ><small
                      >{{ task.id }} · {{ task.createdAt }} ·
                      {{ task.files }} 个 · {{ task.size }}</small
                    >
                  </div>
                  <div class="task-dests">
                    <i
                      v-for="destination in task.destinations"
                      :key="destination.id"
                      :class="['mini-state', destination.state]"
                      >{{ destination.name }} ·
                      {{ taskStateLabel(destination.state)
                      }}{{
                        destination.error ? `：${destination.error}` : ""
                      }}</i
                    >
                  </div>
                  <span :class="['state-pill', task.state]">{{
                    taskStateLabel(task.state)
                  }}</span
                  ><button class="text-button" @click="renameTask(task)">
                    改名</button
                  ><button
                    class="secondary-button compact"
                    @click="openTask(task)"
                  >
                    详情 / 报告
                  </button>
                </article>
                <div v-if="!tasks.length" class="empty-state">
                  没有符合条件的归档记录。
                </div>
              </div>
            </section>
          </template>

          <template v-else-if="page === 'task'">
            <PageHeading
              :eyebrow="`任务 ID · ${selectedTask?.id || '—'}`"
              title="任务详情与报告"
              :description="
                isMock
                  ? '演示任务结果'
                  : '本机真实任务阶段和逐目的地结果；排队不代表完成'
              "
              ><button class="secondary-button" @click="setPage('history')">
                <Archive :size="15" />返回归档记录
              </button></PageHeading
            >
            <section v-if="selectedTask" class="task-detail-layout">
              <div class="panel">
                <div class="panel-heading">
                  <div>
                    <h3>{{ selectedTask.name }}</h3>
                    <p>
                      {{ selectedTask.createdAt }} · 来源：{{
                        selectedTask.source
                      }}
                    </p>
                  </div>
                  <span :class="['state-pill', selectedTask.state]">{{
                    taskStateLabel(selectedTask.state)
                  }}</span>
                </div>
                <div class="detail-progress">
                  <div class="progress-line">
                    <i :style="{ width: `${selectedTask.progress}%` }"></i>
                  </div>
                  <b>{{ selectedTask.progress }}%</b
                  ><span>{{ selectedTask.phase }}</span>
                </div>
                <div
                  v-if="selectedTask.error"
                  class="result-banner failed"
                  role="alert"
                >
                  <b>任务错误</b><span>{{ selectedTask.error }}</span>
                </div>
                <div
                  v-if="resultSummary"
                  :class="['result-banner', resultSummary.state]"
                >
                  <b>{{ resultSummary.headline }}</b
                  ><span>{{ resultSummary.detail }}</span>
                </div>
                <div class="detail-stats">
                  <span
                    ><b>{{ selectedTask.files }}</b> 计划文件</span
                  ><span
                    ><b>{{ selectedTask.size }}</b> 数据量</span
                  ><span
                    ><b>{{ selectedTask.speed }}</b> 当前速度</span
                  >
                </div>
                <p
                  v-if="
                    selectedTask.state === 'running' &&
                    selectedTask.currentFile
                  "
                  class="current-file"
                >
                  正在写入：{{ selectedTask.currentFile }}
                </p>
                <div class="destination-list">
                  <h4>目的地结果</h4>
                  <div
                    v-for="destination in selectedTask.destinations"
                    :key="destination.id"
                    class="destination-row"
                  >
                    <Cloud :size="17" /><span
                      ><b>{{ destination.name }}</b
                      ><small>{{
                        destination.error ||
                        `阶段：${taskStateLabel(destination.state)} · ${destination.progress}%`
                      }}</small></span
                    ><span :class="['state-pill', destination.state]">{{
                      taskStateLabel(destination.state)
                    }}</span
                    ><button
                      v-if="canRetryTarget(selectedTask, destination.id)"
                      class="secondary-button compact"
                      @click="
                        api.history
                          .retry(selectedTask.id, destination.id)
                          .then((task) => {
                            taskDetail = task;
                            say('失败目标正在演示重试。');
                          })
                      "
                    >
                      重试此目标
                    </button>
                  </div>
                </div>
                <div class="panel-actions">
                  <button
                    v-if="canControl(selectedTask.state, 'pause')"
                    class="secondary-button"
                    @click="controlTask('pause')"
                  >
                    暂停</button
                  ><button
                    v-if="canControl(selectedTask.state, 'resume')"
                    class="secondary-button"
                    @click="controlTask('resume')"
                  >
                    继续</button
                  ><button
                    v-if="canControl(selectedTask.state, 'cancel')"
                    class="danger-button"
                    @click="controlTask('cancel')"
                  >
                    取消剩余任务</button
                  ><button class="secondary-button" @click="viewReport">
                    查看报告</button
                  ><button
                    v-if="!isMock"
                    class="secondary-button"
                    @click="exportReport"
                    :disabled="operationBusy"
                  >
                    导出报告</button
                  ><button
                    v-if="
                      !isMock &&
                      selectedTask.state === 'success' &&
                      selectedTask.kind === 'transfer'
                    "
                    class="secondary-button"
                    @click="stageSource"
                    :disabled="operationBusy"
                  >
                    转移任务：再次确认来源暂存
                  </button>
                </div>
              </div>
              <aside class="panel report-panel">
                <h3>报告摘要</h3>
                <p>
                  任务
                  ID、版本、来源/目标、文件数、容量、速度、校验、跳过和失败原因。
                </p>
                <dl class="summary-list">
                  <div>
                    <dt>版本</dt>
                    <dd>V2 本地联调草稿</dd>
                  </div>
                  <div>
                    <dt>完整性</dt>
                    <dd>
                      {{
                        (selectedTask?.verify ?? settings.verify)
                          ? "SHA-256 校验"
                          : "校验已跳过"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>来源安全</dt>
                    <dd>
                      {{
                        selectedTask.kind === "transfer"
                          ? "来源安全以实际报告为准，暂存需再次确认"
                          : "普通备份保留来源"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>同步状态</dt>
                    <dd>
                      {{ isMock ? "离线演示，尚未同步" : "云端同步尚未连接" }}
                    </dd>
                  </div>
                </dl>
              </aside>
            </section>
          </template>

          <template v-else-if="page === 'agent'"
            ><p v-if="showLocalAgentAdapterNotice" class="integration-banner">
              当前桌面环境未接入本机 Agent 模型适配器，Agent 对话不可用。
            </p>
            <PageHeading
              eyebrow="受限工具 · 操作需确认"
              title="Agent 助手"
              description="查找和预览可直接进行；改名、移动、上传、清理等操作先列出对象与目标。"
            ><button class="secondary-button agent-settings-trigger" type="button" aria-label="Agent 设置" title="Agent 设置" @click="agentSettingsOpen = true"><Settings2 :size="15" />设置</button></PageHeading>
            <section class="agent-layout">
              <aside class="panel agent-aside">
                <button
                  class="primary-button full-width"
                  :disabled="modelConsentRef?.isBusy()"
                  @click="requestNewAgentConversation"
                >
                  <Plus :size="15" />新建对话
                </button>
                <h4>最近对话</h4>
                <button v-if="isMock" class="conversation-link">
                  <Sparkles :size="14" />查找海边日落照片
                  <small>今天 10:12</small></button
                ><button v-if="isMock" class="conversation-link">
                  <Archive :size="14" />昨晚的备份任务结果
                  <small>昨天 19:34</small>
                </button>
                <div class="safety-callout">
                  <ShieldCheck :size="16" />所有有副作用的操作都需你确认。
                </div>
              </aside>
              <div class="panel chat-panel">
                <div :class="['chat-scroll', { 'chat-scroll-empty': agentMessages.length === 0 }]">
                  <div class="agent-welcome">
                    <span class="agent-orb"><Sparkles :size="20" /></span
                    ><b>你好，{{ displayName }}</b>
                  </div>
                  <div
                    v-for="(message, index) in agentMessages"
                    :key="index"
                    :class="['message-row', message.role]"
                  >
                    <b v-if="message.role === 'user'">{{ displayName }}</b
                    ><span v-else class="agent-mini">✦</span>
                    <div class="message-content">
                      <p>{{ message.text }}</p>
                      <small v-if="message.reply?.historySync === 'not_synced'">未同步历史（已关闭 Agent 历史同步）</small>
                      <div
                        v-if="message.reply?.matches.length"
                        class="match-list"
                      >
                        <article
                          v-for="match in message.reply.matches"
                          :key="match.kind === 'media' ? match.media.id : match.kind === 'local_media' ? match.media.mediaId : `${match.deviceId}:${match.jobId}`"
                        >
                          <Image :size="15" /><span
                            ><b>{{ match.kind === "media" || match.kind === "local_media" ? match.media.name : match.name }}</b
                            ><small
                              >{{ match.kind === "media" ? `${match.media.path} · ${match.media.quality}` : match.kind === "local_media" ? `${match.media.kind} · ${formatBytes(match.media.bytes)} · ${match.media.qualityState}` : `已同步任务 · ${match.state} · 设备 ${match.deviceId} · ${match.jobId}` }}</small
                            ></span
                          >
                        </article>
                      </div>
                      <div
                        v-if="message.reply?.proposedAction"
                        class="action-plan"
                      >
                        <b>待确认操作计划</b
                        ><span
                          >操作：{{ message.reply.proposedAction.kind === "open" ? "打开" : message.reply.proposedAction.kind }} ·
                          {{ message.reply.proposedAction.label }}</span
                        ><span
                          >对象：{{
                            message.reply.proposedAction.objects.join("、")
                          }}</span
                        ><span
                          >目标：{{ message.reply.proposedAction.target }}</span
                        >
                        <small v-if="message.actionId"
                          >等待你在确认卡片里明确同意或拒绝。</small
                        >
                      </div>
                    </div>
                  </div>
                </div>
                <p v-if="agentHasArchivedUnresolvedTurn" role="status" class="safety-callout">
                  上一轮结果未确认，原记录仍保留在本机；当前是独立新会话。系统不会恢复或重发上一轮。
                </p>
                <ModelConsent :key="agentConversationKey" ref="modelConsentRef" :service="api.model" :query="agentInput" :hide-status="true" :auto-confirm="true" @reply="appendModelReply" @unavailable="say" />
                <form class="composer" @submit.prevent="askAgent">
                  <input
                    v-model="agentInput"
                    placeholder="例如：把今天的 RAW 文件整理到项目目录…"
                  />
                  <select
                    v-if="api.modelProviderConfig"
                    v-model="agentSelectedModel"
                    class="agent-composer-model"
                    aria-label="切换 Agent 模型"
                    :disabled="agentModelBusy || !agentModelConfig || (localWorkspace && agentLocalModels.length === 0)"
                    @change="selectAgentModel"
                  >
                    <option v-if="!agentModelConfig" value="">{{ agentModelBusy ? '读取模型…' : '选择模型' }}</option>
                    <option v-else-if="localWorkspace && agentModelConfig.provider !== 'ollama'" value="" disabled>本机模式已停用云端模型 · 请选择本机 Ollama</option>
                    <option v-else-if="localWorkspace && !agentModelConfig.configured" value="" disabled>本机 Ollama 尚未配置</option>
                    <option v-else :value="agentModelValue">{{ agentModelBusy ? '切换中…' : agentModelLabel }}</option>
                    <option
                      v-if="!localWorkspace && agentModelConfig?.cloudKeyStored && !(agentModelConfig.configured && agentModelConfig.provider !== 'ollama')"
                      :value="`cloud:${agentModelConfig.cloudProvider || agentModelConfig.provider}:${agentModelConfig.cloudModel || agentModelConfig.model}`"
                    >
                      云端 · {{ (agentModelConfig.cloudProvider || agentModelConfig.provider) === 'deepseek' ? 'DeepSeek' : '通义千问' }} · {{ agentModelConfig.cloudModel || agentModelConfig.model }}
                    </option>
                    <option v-for="model in agentLocalModels" :key="model" :value="`local:${model}`">
                      本机 · {{ model }}
                    </option>
                  </select>
                  <button class="primary-button" type="submit" :disabled="!agentCanSend">
                    <Send :size="15" />发送
                  </button>
                </form>
              </div>
            </section>
            <div v-if="agentSettingsOpen" class="modal-backdrop agent-settings-backdrop" @click.self="agentSettingsOpen = false" @keydown.esc="agentSettingsOpen = false">
              <section class="confirm-modal agent-settings-modal" role="dialog" aria-modal="true" aria-labelledby="agent-settings-title">
                <header class="agent-settings-modal-heading"><div><span class="eyebrow">模型与运行方式</span><h2 id="agent-settings-title">Agent 设置</h2></div><button class="icon-button agent-settings-close" type="button" aria-label="关闭 Agent 设置" @click="agentSettingsOpen = false"><X :size="18" /></button></header>
                <div class="agent-settings-body">
                  <OllamaOnboarding :service="api.model" :provider-config="api.modelProviderConfig" />
                  <CloudModelSettings v-if="!localWorkspace" :service="api.modelProviderConfig" />
                  <p v-else class="integration-banner" role="status">
                    本机模式已停用云端模型配置；现有云端 Key 保留在系统钥匙串中，不会被选择或调用。退出本机模式后可在此管理云端模型。
                  </p>
                  <AgentLocalFileActions
                    v-if="api.fileActions"
                    :service="api.fileActions"
                    :matches="latestAgentMatches"
                    :demo="isMock"
                  />
                </div>
              </section>
            </div>
            <AgentActionCard
              v-if="pendingAgentAction?.reply?.proposedAction"
              :action="pendingAgentAction.reply.proposedAction"
              :busy="agentActionBusy"
              @decide="confirmAgent(pendingAgentAction, $event)"
            />
            <div v-if="agentNewConversationReview" class="modal-backdrop" @keydown.esc="agentNewConversationReview = false">
              <section role="alertdialog" aria-modal="true" aria-labelledby="new-agent-conversation-title" class="confirm-modal integration-confirm">
                <h2 id="new-agent-conversation-title">新建独立对话</h2>
                <p>上一轮结果未确认，可能已经运行过本机模型或只读检索。旧轮次记录会保留；新对话会使用新的会话和轮次编号，不会恢复、重发或删除旧轮次。发送新查询仍需单独确认。</p>
                <div class="modal-actions">
                  <button class="secondary-button" @click="agentNewConversationReview = false">保留当前状态</button>
                  <button class="primary-button" @click="confirmNewAgentConversation">保留旧记录并新建对话</button>
                </div>
              </section>
            </div>
          </template>

          <template v-else-if="page === 'account' && !isMock"
            ><PageHeading
              eyebrow="云端未登录"
              title="账号与设备"
              description="身份、设备注册和同步尚未连接。不会展示演示账号或伪造在线状态。"
            />
            <section class="panel">
              <p class="integration-banner">
                本地联调工作区不是登录会话。请等待真实认证服务接入后使用账号与设备功能。
              </p>
              <button class="secondary-button" @click="setPage('settings')">
                查看本机偏好设置
              </button>
            </section></template
          ><template v-else-if="page === 'account'"
            ><p v-if="!isMock" class="integration-banner">
              云端身份和设备同步未连接；开关仅保存本机偏好，不代表云端已同步。
            </p>
            <PageHeading
              eyebrow="个人账号与设备"
              title="账号与设备"
              description="桌面端与 Pocket 共用影序账号；这里管理登录方式、设备会话和可选同步字段。"
            />
            <section class="account-grid">
              <div class="panel account-card">
                <div class="account-profile">
                  <span class="profile-avatar">{{
                    displayName.slice(0, 1)
                  }}</span>
                  <div>
                    <h3>{{ displayName }}</h3>
                    <p>{{ displayEmail }} · 已登录演示账号</p>
                  </div>
                  <span class="state-pill success">本机在线</span>
                </div>
                <h4>登录方式关联</h4>
                <div class="provider-row">
                  <span class="method-icon wechat">微</span
                  ><span
                    ><b>微信扫码</b
                    ><small>账号关联将在认证后端接入时启用</small></span
                  ><button class="secondary-button compact" disabled>
                    待接入
                  </button>
                </div>
                <div class="provider-row">
                  <span class="method-icon">@</span
                  ><span
                    ><b>邮箱账号</b
                    ><small
                      >{{ displayEmail }} · 与 Pocket 邮箱账号兼容入口</small
                    ></span
                  ><button class="text-button">已关联</button>
                </div>
                <button class="danger-button" @click="logout">
                  退出当前账号
                </button>
              </div>
              <div class="panel sync-card">
                <div class="panel-heading">
                  <div>
                    <h3>跨设备同步范围</h3>
                    <p>原片、令牌和精确 GPS/EXIF 不同步。</p>
                  </div>
                </div>
                <label class="setting-row"
                  ><span
                    ><b>备份记录</b
                    ><small>同步任务状态和目的地摘要</small></span
                  ><input
                    type="checkbox"
                    :checked="settings.syncHistory"
                    @change="
                      updateSetting(
                        'syncHistory',
                        ($event.target as HTMLInputElement).checked,
                      )
                    " /></label
                ><label class="setting-row"
                  ><span
                    ><b>Agent 会话</b
                    ><small>用户/助手文本、工具摘要和结果</small></span
                  ><input
                    type="checkbox"
                    :checked="settings.syncAgent"
                    @change="
                      updateSetting(
                        'syncAgent',
                        ($event.target as HTMLInputElement).checked,
                      )
                    " /></label
                ><label class="setting-row"
                  ><span
                    ><b>压缩缩略图</b
                    ><small>最长边约 512px，可随时关闭</small></span
                  ><input
                    type="checkbox"
                    :checked="settings.syncThumbnails"
                    @change="
                      updateSetting(
                        'syncThumbnails',
                        ($event.target as HTMLInputElement).checked,
                      )
                    " /></label
                ><label class="setting-row"
                  ><span
                    ><b>电脑名称与完整路径</b
                    ><small>手机仅可查看路径，不可打开或控制电脑</small></span
                  ><input
                    type="checkbox"
                    :checked="settings.syncPaths"
                    @change="
                      updateSetting(
                        'syncPaths',
                        ($event.target as HTMLInputElement).checked,
                      )
                    "
                /></label>
                <p class="table-foot">
                  数据删除与注销级联清除能力将在真实云端阶段实现。
                </p>
              </div>
            </section>
            <section class="panel devices-panel">
              <div class="panel-heading">
                <div>
                  <h3>设备与会话</h3>
                  <p>
                    {{
                      isMock
                        ? "在线状态和最后同步时间（演示）"
                        : "云端未连接，无可验证设备列表"
                    }}
                  </p>
                </div>
                <button class="text-button" @click="say('设备列表已刷新。')">
                  刷新
                </button>
              </div>
              <article
                v-for="device in devices"
                :key="device.id"
                class="device-row"
              >
                <span class="device-icon"
                  ><component
                    :is="
                      device.platform === 'Android' || device.platform === 'iOS'
                        ? Image
                        : HardDrive
                    "
                    :size="17" /></span
                ><span
                  ><b
                    >{{ device.name }}
                    <small v-if="device.current">当前设备</small></b
                  ><small
                    >{{ device.platform }} · {{ device.lastSeen }}</small
                  ></span
                ><span
                  :class="['state-pill', device.online ? 'success' : 'queued']"
                  >{{ device.online ? "在线" : "离线" }}</span
                ><button
                  v-if="!device.current"
                  class="secondary-button compact"
                  @click="revokeDevice(device)"
                >
                  撤销设备
                </button>
              </article>
            </section>
          </template>

          <template v-else-if="page === 'presets'">
            <PageHeading
              eyebrow="重复使用的导入策略"
              title="导入预设"
              description="保存命名规则、目的地组合和质量筛选策略；每次任务仍需预览确认。"
              ><button class="primary-button" @click="createPreset">
                <Plus :size="15" />新建预设
              </button></PageHeading
            >
            <section class="preset-grid">
              <article
                v-for="(preset, index) in presets"
                :key="preset.id"
                class="panel preset-card"
              >
                <span class="preset-icon"><WandSparkles :size="20" /></span
                ><span v-if="preset.isDefault" class="state-pill success"
                  >默认预设</span
                >
                <h3>{{ presetDisplayName(preset, index) }}</h3>
                <p>{{ preset.pattern }}</p>
                <div class="preset-meta">
                  <span>{{
                    isMock ? "本机保存 · 演示规则" : "本机 SQLite 持久保存"
                  }}</span>
                  <div>
                    <button
                      class="text-button"
                      @click="
                        backupPreset = preset.id;
                        setPage('backup');
                        backupStep = 3;
                      "
                    >
                      应用到备份</button
                    ><button class="text-button" @click="editPreset(preset)">
                      编辑 / 重命名
                    </button
                    ><button
                      v-if="!preset.isDefault"
                      class="text-button danger-text"
                      @click="removePreset(preset)"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </article>
              <article class="panel preset-create">
                <b>新建导入预设</b>
                <p>先填写名称与命名规则，再保存到本机预设列表。</p>
                <button class="secondary-button" @click="createPreset">
                  <Plus :size="14" />配置预设
                </button>
              </article>
            </section>
            <section class="panel notice-card">
              <ShieldCheck :size="17" />
              <div>
                <b>应用前检查</b
                ><span
                  >目标、命名层级和筛选规则会显示在备份摘要；预设不会绕过确认步骤。</span
                >
              </div>
            </section>
          </template>

          <template v-else-if="page === 'health'">
            <PageHeading
              eyebrow="可恢复的数据检查"
              title="恢复与健康审计"
              description="检查已备份副本可用性，并从通过校验的副本恢复到新位置。"
              ><button class="primary-button" @click="runAudit">
                <Activity :size="15" />{{
                  isMock ? "运行健康审计（演示）" : "运行本地健康审计"
                }}
              </button></PageHeading
            >
            <section class="panel">
              <div v-if="auditResult" class="audit-summary">
                <div>
                  <b>{{ auditResult.checked }}</b
                  ><span>已抽查文件</span>
                </div>
                <div>
                  <b>{{ auditResult.healthy }}</b
                  ><span>校验正常</span>
                </div>
                <div>
                  <b>{{ auditResult.issues.length }}</b
                  ><span>需处理项目</span>
                </div>
                <span class="demo-label">{{ auditResult.lastRun }}</span>
              </div>
              <div v-else class="empty-state">
                <ShieldCheck :size="28" /><b>尚未运行审计</b
                ><span>审计只检查备份副本，不会修改来源或目标文件。</span
                ><button class="secondary-button" @click="runAudit">
                  {{ isMock ? "开始抽查（演示）" : "开始逐副本校验" }}
                </button>
              </div>
              <div v-if="auditResult" class="issue-list">
                <article
                  v-for="issue in auditResult.issues"
                  :key="issue.path"
                  class="issue-row"
                >
                  <span class="status-dot running"></span
                  ><span
                    ><b>{{ issue.path }}</b
                    ><small
                      >{{ issue.reason }} ·
                      {{
                        issue.recoverable
                          ? "可从其他健康副本恢复"
                          : "需要人工检查"
                      }}</small
                    ></span
                  ><button
                    class="secondary-button compact"
                    @click="
                      say(
                        `恢复预览：${issue.path} → 指定目标。未执行文件写入。`,
                      )
                    "
                  >
                    预览恢复
                  </button>
                </article>
              </div>
            </section>
            <section v-if="isMock" class="panel restore-panel">
              <h3>恢复文件</h3>
              <p>选择健康副本与另存位置；默认不覆盖已有文件。</p>
              <div class="two-column-fields">
                <label
                  ><span>恢复来源</span
                  ><select class="select-field">
                    <option>已校验备份副本 · 山野纪行</option>
                  </select></label
                ><label
                  ><span>恢复目标</span><input value="桌面 / 恢复文件（演示）"
                /></label>
              </div>
              <button
                class="primary-button"
                @click="
                  say(
                    '恢复流程将重新计算 SHA-256 并提供同名文件另存选项；当前仅展示。',
                  )
                "
              >
                选择文件并预览恢复
              </button>
            </section>
            <section v-else class="panel restore-panel">
              <h3>从已校验副本恢复</h3>
              <p>
                先运行审计，再选择健康副本。输出目录通过原生选择器确定，并在写入前确认。
              </p>
              <label class="field-label">健康副本</label
              ><div class="restore-controls"><select v-model="restorePath" class="select-field">
                <option
                  v-for="replica in healthyReplicas"
                  :key="replica.id"
                  :value="replica.path"
                >
                  {{ replica.path }}
              </option></select><button
                class="primary-button"
                @click="restoreHealthyReplica"
                :disabled="!restorePath || operationBusy"
              >
                选择目标并预览恢复
              </button></div>
              <p v-if="!healthyReplicas.length">尚无本轮审计通过的副本。</p>
            </section>
          </template>

          <template v-else-if="page === 'delivery'">
            <PageHeading
              eyebrow="原片保持不变"
              title="客户交付包"
              description="将选中素材副本写入独立目录，可附联系表、清单与 SHA-256 校验文件。"
            />
            <section class="delivery-layout">
              <div class="panel">
                <div class="panel-heading">
                  <div>
                    <h3>选择交付素材</h3>
                    <p>
                      {{
                        isMock
                          ? "照片、视频与已批准的副本（演示样本）"
                          : "仅从已校验的本地副本创建交付包"
                      }}
                    </p>
                  </div>
                  <span class="demo-label"
                    >{{ deliverySelection.length }} 项已选</span
                  >
                </div>
                <label
                  v-for="file in files.filter((f) => f.kind !== 'other')"
                  :key="file.id"
                  class="delivery-file"
                  ><input
                    v-model="deliverySelection"
                    type="checkbox"
                    :value="file.id"
                  /><span class="delivery-file-main"
                    ><b>{{ file.name }}</b
                    ><small
                      >{{ file.capturedAt }} · {{ file.size }}</small
                    ></span
                  ><span class="delivery-file-quality">{{
                    file.quality
                  }}</span></label
                >
              </div>
              <div class="panel">
                <h3>交付设置</h3>
                <label class="field-label">交付包名称</label
                ><input v-model="deliveryName" /><label class="check-row"
                  ><input v-model="deliveryContact" type="checkbox" />生成联系表
                  / 预览图</label
                ><label class="check-row"
                  ><input
                    v-model="deliveryManifest"
                    type="checkbox"
                  />包含文件清单与 SHA-256 校验文件</label
                >
                <div class="consent-box">
                  <ShieldCheck :size="16" /><span
                    >交付包写入独立目录；不会移动或改写原始素材。</span
                  >
                </div>
                <button
                  class="primary-button full-width"
                  @click="createDelivery"
                  :disabled="!deliverySelection.length"
                >
                  <PackageGlyph />{{
                    isMock ? "生成交付包（演示）" : "预检并生成交付任务"
                  }}
                </button>
              </div>
            </section>
            <section class="panel">
              <div class="panel-heading">
                <div>
                  <h3>最近交付包</h3>
                  <p>
                    {{
                      isMock
                        ? "生成记录（仅演示）"
                        : "生成结果请查看对应本地任务及报告"
                    }}
                  </p>
                </div>
              </div>
              <p class="table-foot">
                创建交付包后显示生成摘要、文件数和清单状态。
              </p>
            </section>
          </template>

          <template v-else>
            <PageHeading
              eyebrow="桌面端配置"
              title="偏好设置"
              description="配置主题、备份、智能整理、云盘、通知、同步和隐私偏好。"
              ><button class="secondary-button" @click="resetSettings">
                恢复默认值
              </button></PageHeading
            >
            <div class="settings-layout">
              <nav class="panel settings-nav">
                <a href="#general">通用</a
                ><a href="#backup-settings">备份与校验</a
                ><a href="#naming">命名与 AI</a><a href="#quality">质量分析</a
                ><a href="#cloud">云盘与同步</a
                ><a href="#advanced">通知与高级</a
                ><a href="#updates">版本更新</a
                ><a href="#privacy">隐私安全</a>
              </nav>
              <div class="settings-content">
                <section id="general" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>通用与外观</h3>
                      <p>跨 Windows/macOS 的桌面偏好</p>
                    </div>
                  </div>
                  <label class="setting-row"
                    ><span><b>外观主题</b><small>保持移动端语义色</small></span
                    ><select
                      class="select-field small-select"
                      :value="settings.theme"
                      @change="
                        updateSetting(
                          'theme',
                          ($event.target as HTMLSelectElement)
                            .value as AppSettings['theme'],
                        )
                      "
                    >
                      <option value="light">浅色</option>
                      <option value="dark">深色</option>
                    </select></label
                  ><label class="setting-row"
                    ><span
                      ><b>登录桌面后启动</b
                      ><small>{{
                        isMock
                          ? "开机启动演示偏好"
                          : "当前未生效 · 仅保存偏好；本版本尚不支持开机启动"
                      }}</small></span
                    ><input
                      type="checkbox"
                      :checked="isMock ? settings.startOnLogin : false"
                      :disabled="!isMock"
                      :title="isMock ? '演示偏好' : '开机启动能力尚未接入，当前偏好不生效'"
                      @change="
                        updateSetting(
                          'startOnLogin',
                          ($event.target as HTMLInputElement).checked,
                        )
                      " /></label
                  ><label class="setting-row"
                    ><span
                      ><b>关闭窗口后后台运行</b
                      ><small>{{
                        isMock
                          ? "后台运行演示偏好"
                          : "当前未启用 · 托盘/菜单栏能力尚未接入"
                      }}</small></span
                    ><input
                      type="checkbox"
                      :checked="isMock ? settings.backgroundOnClose : false"
                      :disabled="!isMock"
                      :title="isMock ? '演示偏好' : '后台运行能力尚未接入，当前未启用'"
                      @change="
                        updateSetting(
                          'backgroundOnClose',
                          ($event.target as HTMLInputElement).checked,
                        )
                      "
                  /></label>
                  <div class="account-credential-settings">
                    <div class="account-credential-heading">
                      <div>
                        <b>账户安全</b>
                        <small>{{
                          localWorkspace
                            ? "本机模式没有云端账户；登录影序账号后可管理用户名和密码。"
                            : isMock
                              ? "浏览器预览只展示交互，不会修改真实账号。"
                              : `账户邮箱：${displayEmail}`
                        }}</small>
                      </div>
                      <span v-if="accountUsername" class="state-pill">用户名：{{ accountUsername }}</span>
                    </div>
                    <div class="setting-row">
                      <span><b>登录用户名</b><small>修改后，下次可使用新用户名登录。</small></span>
                      <button class="secondary-button compact" :disabled="localWorkspace || (!session && !isMock)" @click="openAccountDialog('username')">修改用户名</button>
                    </div>
                    <div class="setting-row">
                      <span><b>登录密码</b><small>修改密码需要当前密码和邮箱验证码。</small></span>
                      <button class="secondary-button compact" :disabled="localWorkspace || (!session && !isMock)" @click="openAccountDialog('password')">修改密码</button>
                    </div>
                  </div>
                </section>
                <section id="backup-settings" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>备份、校验与冲突</h3>
                      <p>安全默认值与 SHA-256 行为</p>
                    </div>
                  </div>
                  <label class="setting-row"
                    ><span
                      ><b>复制后校验</b
                      ><small>可在任务摘要逐次调整</small></span
                    ><input
                      type="checkbox"
                      :checked="settings.verify"
                      @change="
                        updateSetting(
                          'verify',
                          ($event.target as HTMLInputElement).checked,
                        )
                      "
                  /></label>
                  <div class="setting-row">
                    <span
                      ><b>同名文件策略</b
                      ><small>内容相同跳过；不同内容添加序号</small></span
                    ><span class="state-pill success">不覆盖原文件</span>
                  </div>
                  <div class="setting-row">
                    <span
                      ><b>本机空间与已备份统计</b
                      ><small>{{
                        isMock
                          ? "显示 384 GB / 1 TB · 演示值"
                          : `已索引 ${summary.totalSize}，目标空闲空间在预检时检查`
                      }}</small></span
                    ><HardDrive :size="16" />
                  </div>
                </section>
                <section id="naming" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>命名与智能整理</h3>
                      <p>命名先预览；模型不直接执行文件操作</p>
                    </div>
                  </div>
                  <label v-if="!localWorkspace" class="setting-row"
                    ><span
                      ><b>AI 命名建议</b
                      ><small>离线备份不依赖 AI；执行前仍需确认</small></span
                    ><select
                      v-model="settings.aiProvider"
                      class="select-field small-select"
                      @change="updateSetting('aiProvider', settings.aiProvider)"
                    >
                      <option>本地规则优先</option>
                      <option>Ollama</option>
                      <option v-if="!localWorkspace">OpenAI 兼容 API</option>
                    </select></label
                  >
                  <p v-else class="cloud-disabled-note">本机模式不使用云端模型；Agent 仅连接本机 Ollama，命名建议使用本机规则。</p>
                  <div class="setting-row">
                    <span
                      ><b>当前命名预设</b
                      ><small>{{
                        presets.find((p) => p.isDefault)?.pattern ||
                        "日期 / 事件 / 设备"
                      }}</small></span
                    ><button class="text-button" @click="setPage('presets')">
                      管理预设
                    </button>
                  </div>
                </section>
                <section id="quality" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>影像质量与相似度</h3>
                      <p>候选只标记和隔离，不静默删除</p>
                    </div>
                  </div>
                  <label class="setting-row"
                    ><span
                      ><b>自动生成质量建议</b
                      ><small>{{
                        isMock
                          ? "照片/视频分别分析"
                          : "当前手动分析可用；自动分析调度尚未接入"
                      }}</small></span
                    ><input
                      type="checkbox"
                      :checked="settings.autoAnalyze"
                      @change="
                        updateSetting(
                          'autoAnalyze',
                          ($event.target as HTMLInputElement).checked,
                        )
                      " /></label
                  ><label class="setting-row"
                    ><span
                      ><b>视觉相似度阈值</b
                      ><small
                        >当前 {{ settings.similarity }}% · 不等同于 SHA-256
                        重复</small
                      ></span
                    ><input
                      class="range-control"
                      type="range"
                      min="50"
                      max="99"
                      :value="settings.similarity"
                      @input="
                        updateSetting(
                          'similarity',
                          Number(($event.target as HTMLInputElement).value),
                        )
                      "
                  /></label>
                </section>
                <section id="cloud" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>{{ localWorkspace ? "百度网盘与云同步（已停用）" : "百度网盘与云同步" }}</h3>
                      <p>{{ localWorkspace ? "本机模式不发起任何网盘或云同步请求。" : "仅接官方允许的 API 与个人账号授权" }}</p>
                    </div>
                  </div>
                  <template v-if="localWorkspace">
                    <p class="cloud-disabled-note">本机模式下云账号、跨设备同步和网盘授权均不可用；相关操作不会发出远端请求。</p>
                  </template>
                  <template v-else>
                    <NetdiskPanel v-if="api.netdisk" :api="api.netdisk" />
                    <p v-else>浏览器演示不连接真实网盘。请在原生应用查看本机资质、授权与上传队列。</p>
                    <div class="setting-row">
                      <span><b>同步速度上限</b><small>0 表示不限制 · {{ settings.maxUploadMbps }} Mbps</small></span>
                      <input class="number-control" type="number" min="0" :value="settings.maxUploadMbps" @change="updateSetting('maxUploadMbps', Number(($event.target as HTMLInputElement).value))" />
                    </div>
                    <button class="text-button" @click="setPage('account')">管理同步范围与设备 <ArrowRight :size="13" /></button>
                  </template>
                </section>
                <section id="advanced" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>通知与高级选项</h3>
                      <p>Webhook、带宽和相似度</p>
                    </div>
                  </div>
                  <label v-if="!localWorkspace" class="setting-row"
                    ><span
                      ><b>Webhook 通知</b
                      ><small>真实发送需后端配置并由用户授权</small></span
                    ><input
                      type="checkbox"
                      :checked="settings.webhookEnabled"
                      @change="
                        updateSetting(
                          'webhookEnabled',
                          ($event.target as HTMLInputElement).checked,
                        )
                      " /></label
                  ><label v-if="settings.webhookEnabled && !localWorkspace" class="setting-row"
                    ><span
                      ><b>Webhook 地址</b
                      ><small>演示表单；请勿填写真实密钥</small></span
                    ><input
                      v-model="settings.webhookUrl"
                      class="text-control"
                      placeholder="https://example.com/hook" /></label
                  ><p v-if="localWorkspace" class="cloud-disabled-note">本机模式不会配置或发送 Webhook 请求。</p>
                  <button
                    class="secondary-button"
                    @click="say('高级参数已保存至演示状态。')"
                  >
                    保存高级设置
                  </button>
                </section>
                <section id="updates" class="panel settings-section update-section">
                  <div class="panel-heading">
                    <div>
                      <h3>版本更新</h3>
                      <p>从影序 GitHub Releases 检查桌面版更新</p>
                    </div>
                    <button class="secondary-button" :disabled="updateCheckBusy" @click="checkAppUpdates">
                      {{ updateCheckBusy ? '正在检查…' : '检查更新' }}
                    </button>
                  </div>
                  <div class="update-current-row">
                    <span><b>当前版本</b><small>桌面端</small></span>
                    <code>{{ appVersion }}</code>
                  </div>
                  <p v-if="updateCheckError" class="error-note" role="alert">{{ updateCheckError }}</p>
                  <div v-else-if="updateCheckResult" class="update-result" role="status" aria-live="polite">
                    <template v-if="updateCheckResult.kind === 'no-release'">
                      <b>暂无可用桌面版本</b><span>目前没有包含此平台安装包的公开 Release。你可以查看发布页了解后续版本。</span>
                      <button class="text-button" @click="openReleaseList">查看发布页 <ArrowRight :size="13" /></button>
                    </template>
                    <template v-else-if="updateCheckResult.kind === 'up-to-date'">
                      <b>当前已是最新版本</b><span>已与公开版本 {{ updateCheckResult.latestVersion }} 比较。</span>
                    </template>
                    <template v-else>
                      <b>发现新版本 {{ updateCheckResult.latestVersion }}</b>
                      <span>{{ updateCheckResult.notes || '新版本已发布，可前往 GitHub Releases 查看说明与下载文件。' }}</span>
                      <button class="primary-button" @click="openAppUpdate">{{ updateCheckResult.downloadUrl ? '下载桌面更新' : '查看版本与下载' }} <ArrowRight :size="14" /></button>
                    </template>
                  </div>
                  <p v-else class="update-hint">检查时只读取公开版本信息，不会自动下载或替换应用。</p>
                  <small v-if="updateCheckedAt" class="update-timestamp">上次检查：{{ updateCheckedAt }}</small>
                </section>
                <section id="privacy" class="panel settings-section">
                  <div class="panel-heading">
                    <div>
                      <h3>隐私与安全</h3>
                      <p>敏感数据默认最小化同步</p>
                    </div>
                  </div>
                  <div class="consent-box">
                    <LockKeyhole :size="17" /><span
                      >原片、访问令牌和精确 GPS/EXIF 不上传影序云端。Agent
                      不执行任意 Shell。</span
                    >
                  </div>
                  <label class="check-row"
                    ><input
                      type="checkbox"
                      checked
                      disabled
                    />有副作用操作逐次展示对象、目标与影响并确认</label
                  >
                </section>
              </div>
            </div>
          </template>
        </div>
      </div>
      <footer class="statusbar">
        <span
          ><i class="status-dot online"></i
          >{{ isMock ? "浏览器演示模式" : localWorkspace ? "本机模式 · 不登录、不云同步" : "本地后端" }}</span
        ><span>{{
          isMock
            ? "MockDesktopApi · 不读取磁盘"
            : localWorkspace ? "Rust / SQLite · 云账号、同步和网盘功能已停用" : "Rust / SQLite · 云端账号和 Agent 未连接"
        }}</span
        ><span class="statusbar-right"
          ><button class="statusbar-help" type="button" disabled title="帮助与反馈入口尚未接入">
            <CircleHelp :size="13" />帮助与反馈（暂未接入）
          </button><i></i>{{ localWorkspace ? "本机模式" : "V2 草稿预览" }}</span
        >
      </footer>
    </section>
    <div v-if="showModal === 'clear-history'" class="modal-backdrop">
      <section class="confirm-modal">
        <button class="modal-close" @click="showModal = ''">×</button
        ><span class="modal-icon"><Archive :size="22" /></span>
        <h2>清空归档记录？</h2>
        <p>此操作会清空本机归档记录和报告，不会删除已备份媒体文件。</p>
        <div class="modal-actions">
          <button class="secondary-button" @click="showModal = ''">取消</button
          ><button class="danger-button" @click="clearHistory">确认清空</button>
        </div>
      </section>
    </div>
    <div v-if="presetEditorOpen" class="modal-backdrop" @click.self="presetEditorOpen = false">
      <form class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="preset-editor-title" @submit.prevent="savePreset">
        <button class="modal-close" type="button" aria-label="关闭" @click="presetEditorOpen = false">×</button>
        <h2 id="preset-editor-title">{{ presetEditingId ? "编辑导入预设" : "新建导入预设" }}</h2>
        <label class="field-label" for="preset-name">预设名称</label>
        <input id="preset-name" v-model="presetDraftName" maxlength="80" required placeholder="例如：旅行项目归档" />
        <label class="field-label" for="preset-pattern">命名规则</label>
        <input id="preset-pattern" v-model="presetDraftPattern" maxlength="160" required placeholder="日期 / 事件 / 设备" />
        <p>保存后可在备份流程中应用；每次任务仍会显示摘要并等待确认。</p>
        <div class="modal-actions">
          <button class="secondary-button" type="button" @click="presetEditorOpen = false">取消</button>
          <button class="primary-button" type="submit">保存预设</button>
        </div>
      </form>
    </div>
    <div v-if="presetToDelete" class="modal-backdrop" @click.self="presetToDelete = null">
      <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-preset-title">
        <button class="modal-close" aria-label="关闭" @click="presetToDelete = null">×</button>
        <h2 id="delete-preset-title">删除导入预设？</h2>
        <p>将删除“{{ presetDisplayName(presetToDelete, 0) }}”。不会影响已创建的任务或媒体文件。</p>
        <div class="modal-actions">
          <button class="secondary-button" @click="presetToDelete = null">取消</button>
          <button class="danger-button" @click="confirmRemovePreset">确认删除</button>
        </div>
      </section>
    </div>
    <div v-if="taskToRename" class="modal-backdrop" @click.self="taskToRename = null">
      <form class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="rename-task-title" @submit.prevent="saveTaskRename">
        <button class="modal-close" type="button" aria-label="关闭" @click="taskToRename = null">×</button>
        <h2 id="rename-task-title">重命名归档记录</h2>
        <p>只修改记录名称，不会改动备份文件。</p>
        <label class="field-label" for="task-rename">记录名称</label>
        <input id="task-rename" v-model="taskRenameDraft" maxlength="120" required />
        <div class="modal-actions">
          <button class="secondary-button" type="button" @click="taskToRename = null">取消</button>
          <button class="primary-button" type="submit">保存名称</button>
        </div>
      </form>
    </div>
  </div>
  <div v-if="accountDialog" class="modal-backdrop" @click.self="closeAccountDialog">
    <form
      v-if="accountDialog === 'username'"
      class="confirm-modal account-editor-form"
      role="dialog"
      aria-modal="true"
      aria-labelledby="account-username-title"
      novalidate
      @submit.prevent="saveAccountUsername"
    >
      <button class="modal-close" type="button" aria-label="关闭" @click="closeAccountDialog">×</button>
      <div class="modal-icon"><LockKeyhole :size="19" /></div>
      <h2 id="account-username-title">修改登录用户名</h2>
      <p>新用户名需为 2 至 48 位，并以英文字母或数字开头。修改成功后，下次登录请使用新用户名。</p>
      <label class="field-label" for="account-username">新用户名</label>
      <input id="account-username" v-model="accountUsernameDraft" autocomplete="username" maxlength="48" required placeholder="请输入新用户名" />
      <p v-if="isMock" class="account-editor-note">浏览器预览不连接账户服务，不会修改真实账号。</p>
      <p v-if="accountError" class="error-note" role="alert">{{ accountError }}</p>
      <div class="modal-actions">
        <button class="secondary-button" type="button" @click="closeAccountDialog">取消</button>
        <button class="primary-button" type="submit" :disabled="accountBusy">{{ accountBusy ? "正在保存…" : "保存用户名" }}</button>
      </div>
    </form>
    <form
      v-else
      class="confirm-modal account-editor-form"
      role="dialog"
      aria-modal="true"
      aria-labelledby="account-password-title"
      novalidate
      @submit.prevent="saveAccountPassword"
    >
      <button class="modal-close" type="button" aria-label="关闭" @click="closeAccountDialog">×</button>
      <div class="modal-icon"><LockKeyhole :size="19" /></div>
      <h2 id="account-password-title">修改登录密码</h2>
      <p>为保护账号，需要当前密码和发送到绑定邮箱的 6 位验证码。验证码 5 分钟内有效。</p>
      <label class="field-label" for="account-old-password">当前密码</label>
      <input id="account-old-password" v-model="accountOldPassword" type="password" autocomplete="current-password" required />
      <label class="field-label" for="account-new-password">新密码</label>
      <input id="account-new-password" v-model="accountNewPassword" type="password" autocomplete="new-password" minlength="8" maxlength="64" required placeholder="8–64 位，含大小写字母、数字和特殊字符" />
      <label class="field-label" for="account-confirm-password">确认新密码</label>
      <input id="account-confirm-password" v-model="accountConfirmPassword" type="password" autocomplete="new-password" minlength="8" maxlength="64" required />
      <label class="field-label" for="account-verification-code">邮箱验证码</label>
      <div class="account-code-row">
        <input id="account-verification-code" v-model="accountVerificationCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required placeholder="请输入 6 位验证码" />
        <button class="secondary-button compact" type="button" :disabled="accountBusy || accountCodeSent" @click="sendAccountChangeCode">{{ accountBusy ? "发送中…" : accountCodeSent ? "已发送" : "获取验证码" }}</button>
      </div>
      <p v-if="isMock" class="account-editor-note">浏览器预览不发送验证码，也不会修改真实账号。</p>
      <p v-if="accountError" class="error-note" role="alert">{{ accountError }}</p>
      <div class="modal-actions">
        <button class="secondary-button" type="button" @click="closeAccountDialog">取消</button>
        <button class="primary-button" type="submit" :disabled="accountBusy">{{ accountBusy ? "正在保存…" : "确认修改" }}</button>
      </div>
    </form>
  </div>
  <div v-if="pendingConfirmation" class="modal-backdrop" @keydown="modalKeys">
    <section
      class="confirm-modal integration-confirm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-heading"
    >
      <h2 id="confirm-heading">{{ pendingConfirmation.title }}</h2>
      <p>{{ pendingConfirmation.description }}</p>
      <dl class="summary-list">
        <div v-for="row in pendingConfirmation.rows" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
      <details v-if="pendingConfirmation.files?.length">
        <summary>文件列表预览</summary>
        <ul>
          <li v-for="file in pendingConfirmation.files" :key="file">
            {{ file }}
          </li>
        </ul>
      </details>
      <div class="modal-actions">
        <button class="secondary-button" @click="finishConfirmation(false)">
          取消</button
        ><button class="primary-button" @click="finishConfirmation(true)">
          {{ pendingConfirmation.confirmLabel || "确认执行" }}
        </button>
      </div>
    </section>
  </div>
  <div v-if="analysis || taskReport" class="modal-backdrop">
    <section
      class="confirm-modal integration-result"
      role="dialog"
      aria-modal="true"
      aria-label="本地分析或报告"
    >
      <button
        class="modal-close"
        @click="
          analysis = null;
          taskReport = null;
        "
        aria-label="关闭"
      >
        ×
      </button>
      <h2>{{ taskReport ? "本地任务报告" : analysisTitle }}</h2>
      <p>本机结果，未上传原片；质量候选需人工判断。</p>
      <dl class="summary-list">
        <div v-for="row in resultRows" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
      <details v-if="canShowLocalDiagnostics">
        <summary>开发联调诊断 JSON</summary>
        <pre>{{ JSON.stringify(taskReport || analysis, null, 2) }}</pre>
      </details>
      <button
        class="secondary-button"
        @click="
          analysis = null;
          taskReport = null;
        "
      >
        关闭
      </button>
    </section>
  </div>
</template>

<script lang="ts">
import { defineComponent, h } from "vue";
const PageHeading = defineComponent({
  props: { eyebrow: String, title: String, description: String },
  setup(props, { slots }) {
    return () =>
      h("header", { class: "page-heading-row" }, [
        h("div", [
          h("span", { class: "eyebrow" }, props.eyebrow),
          h("h1", props.title),
          h("p", props.description),
        ]),
        slots.default?.(),
      ]);
  },
});
const PackageGlyph = defineComponent({
  setup() {
    return () =>
      h("span", { class: "package-glyph", "aria-hidden": "true" }, "▣");
  },
});
export default { components: { PageHeading, PackageGlyph } };
</script>
