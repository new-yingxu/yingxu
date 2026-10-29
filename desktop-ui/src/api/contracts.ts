import type { LocalDirectoryGrant, LocalFileActionPreview, LocalFileActionReceipt } from './local-file-actions'

export type Result<T> =
  { ok: true; data: T } | { ok: false; code: string; message: string };
export type TaskState =
  | "queued"
  | "running"
  | "paused"
  | "success"
  | "partial"
  | "cancelled"
  | "failed";
export type MediaKind = "photo" | "video" | "other";

export interface UserSession {
  userId: string;
  displayName: string;
  email: string;
  deviceId: string;
  expiresAt: string;
}
export interface Device {
  id: string;
  name: string;
  platform: "macOS" | "Windows" | "Android" | "iOS";
  online: boolean;
  lastSeen: string;
  current?: boolean;
}
export interface TaskDestination {
  id: string;
  name: string;
  state: TaskState;
  progress: number;
  error?: string;
}
export interface MediaFile {
  id: string;
  name: string;
  path: string;
  kind: MediaKind;
  size: string;
  capturedAt: string;
  device: string;
  location: string;
  quality: string;
  qualityState: "good" | "review" | "unanalyzed";
  selected?: boolean;
}
export interface MediaPage {
  items: MediaFile[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
}
export interface ArchiveTask {
  kind?: string;
  verify?: boolean;
  id: string;
  name: string;
  state: TaskState;
  createdAt: string;
  files: number;
  size: string;
  progress: number;
  speed: string;
  phase: string;
  currentFile?: string;
  source: string;
  destinations: TaskDestination[];
  error?: string;
}
export interface NamePreset {
  id: string;
  name: string;
  pattern: string;
  isDefault: boolean;
}
export interface AppSettings {
  theme: "light" | "dark";
  startOnLogin: boolean;
  backgroundOnClose: boolean;
  verify: boolean;
  autoAnalyze: boolean;
  similarity: number;
  maxUploadMbps: number;
  syncHistory: boolean;
  syncAgent: boolean;
  syncThumbnails: boolean;
  syncPaths: boolean;
  webhookEnabled: boolean;
  webhookUrl: string;
  aiProvider: string;
}
export type AgentMatch =
  | { kind: "media"; media: MediaFile }
  | { kind: "local_media"; media: { mediaId: string; name: string; kind: MediaKind; bytes: number; qualityState: "good" | "review" | "unanalyzed" } }
  | { kind: "job"; deviceId: string; jobId: string; name: string; state: string; sequence: number; occurredAt: string; source: "synced_job_snapshot" };

export interface AgentReply {
  duplicate?: boolean;
  historySync?: "synced" | "not_synced";
  orchestrator?: "langgraph";
  model?: string;
  provider?: string;
  text: string;
  matches: AgentMatch[];
  proposedAction?: {
    actionId?: string;
    tool?: string;
    fileIds?: string[];
    expiresAt?: string;
    canConfirm?: boolean;
    /** Who proposed the operation. Shown on the confirmation card. */
    source?: string;
    kind: "open" | "rename" | "move" | "upload" | "cleanup";
    label: string;
    target: string;
    objects: string[];
  };
}
export interface AuditResult {
  checked: number;
  healthy: number;
  issues: { path: string; reason: string; recoverable: boolean }[];
  lastRun: string;
}
export interface UnconfiguredModelStatus {
  configured: false;
  requiresExplicitConsent: true;
  executionAuthorized: false;
  /** Present only for local Agent status; Cloud model status stays transport-neutral. */
  availability?: LocalAgentAvailability;
  cloudFallback?: false;
}
export interface ConfiguredModelStatus {
  configured: true;
  provider: string;
  model: string;
  region: string;
  requiresExplicitConsent: true;
  executionAuthorized: false;
  /** Present only for local Agent status; Cloud model status stays transport-neutral. */
  availability?: LocalAgentAvailability;
  cloudFallback?: false;
}
export type LocalAgentAvailability = "ready" | "configured" | "ollama_unavailable" | "model_not_installed" | "sidecar_unavailable";
export interface DeliveryPackage {
  id: string;
  name: string;
  items: number;
  includeContactSheet: boolean;
  includeManifest: boolean;
  createdAt: string;
}

/** UI contract: native adapter maps to local RPC; browser preview uses explicit mock data. */
export interface DesktopApi {
  fileActions: LocalFileActionService;
  auth: {
    restore(): Promise<UserSession | null>;
    login(
      method: "wechat" | "phone-code" | "email-code" | "email-password",
      identifier: string,
      proof: string,
    ): Promise<UserSession>;
    logout(): Promise<void>;
  };
  workbench: {
    snapshot(): Promise<{
      tasks: ArchiveTask[];
      fileCount: number;
      totalSize: string;
      destinations: number;
      reviewCount: number;
    }>;
  };
  backup: {
    scan(): Promise<{
      scanReport?: ScanReport;
    sources: {
        id: string;
        name: string;
        path: string;
        files: number;
        size: string;
        free: string;
      }[];
    }>;
    start(payload: {
      sourceId: string;
      destinations: string[];
      presetId: string;
      verify: boolean;
    }): Promise<ArchiveTask>;
    control(
      taskId: string,
      action: "pause" | "resume" | "cancel" | "retry",
    ): Promise<ArchiveTask>;
  };
  transfer: {
    inspect(source: string, target: string): Promise<MediaFile[]>;
    start(
      files: string[],
      target: string,
      confirmSourceRemoval: boolean,
    ): Promise<ArchiveTask>;
  };
  library: {
    searchPage(
      query: string,
      kind: MediaKind | "all",
      quality: string,
      limit?: number,
      offset?: number,
    ): Promise<MediaPage>;
    search(
      query: string,
      kind: MediaKind | "all",
      quality: string,
    ): Promise<MediaFile[]>;
  };
  history: {
    list(query: string, state: TaskState | "all"): Promise<ArchiveTask[]>;
    detail(taskId: string): Promise<ArchiveTask>;
    rename(taskId: string, name: string): Promise<void>;
    clear(): Promise<void>;
    retry(taskId: string, destinationId: string): Promise<ArchiveTask>;
  };
  agent: {
    ask(text: string): Promise<AgentReply>;
    confirm(actionId: string, accepted: boolean): Promise<string | AgentReply>;
  };
  account: {
    devices(): Promise<Device[]>;
    revoke(deviceId: string): Promise<void>;
    updateSync(
      settings: Pick<
        AppSettings,
        "syncHistory" | "syncAgent" | "syncThumbnails" | "syncPaths"
      >,
    ): Promise<void>;
  };
  settings: {
    get(): Promise<AppSettings>;
    update(settings: Partial<AppSettings>): Promise<AppSettings>;
    reset(): Promise<AppSettings>;
    connectProvider(provider: "baidu" | "quark"): Promise<string>;
  };
  presets: {
    list(): Promise<NamePreset[]>;
    save(preset: NamePreset): Promise<NamePreset>;
    remove(id: string): Promise<void>;
  };
  audit: {
    run(): Promise<AuditResult>;
    restore(path: string, destination: string): Promise<string>;
  };
  delivery: {
    create(
      name: string,
      fileIds: string[],
      includeContactSheet: boolean,
      includeManifest: boolean,
    ): Promise<DeliveryPackage>;
  };
}

export interface BackupPayload {
  sourceId: string;
  destinations: string[];
  presetId: string;
  verify: boolean;
  nameValues?: Record<string, string>;
}
export interface Confirmation {
  title: string;
  description: string;
  rows: { label: string; value: string }[];
  files?: string[];
  confirmLabel?: string;
}
export interface LocalCapabilities {
  rawPreview: boolean;
  rawFullDecode: boolean;
  rawFormats: string[];
  videoMetadata: boolean;
  videoFrameExtraction: boolean;
  videoContainers: string[];
  netdiskRealUpload: boolean;
  netdiskProviders: {
    id: string;
    approval: string;
    realUploadAvailable: boolean;
  }[];
}
export interface MediaAnalysis {
  mediaId?: string;
  thumbnailAvailable?: boolean;
  fullDecodePerformed?: boolean;
  frameSource?: string;
  framesUploaded?: boolean;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface LocalIntegration {
  replicas(): Promise<{ id: string; path: string; verified: boolean }[]>;
  health(): Promise<Record<string, unknown>>;
  capabilities(): Promise<LocalCapabilities>;
  chooseDirectory(purpose: "source" | "destination"): Promise<string>;
  indexSource(): Promise<IndexResult>;
  lastIndexReport(): IndexResult | null;
  analyze(mediaId: string, kind: MediaKind): Promise<MediaAnalysis>;
  thumbnail(mediaId: string): Promise<string>;
  report(jobId: string): Promise<Record<string, unknown>>;
  exportReport(
    jobId: string,
    format: "json" | "md" | "txt" | "csv",
  ): Promise<string>;
  startBackup(payload: BackupPayload): Promise<ArchiveTask>;
  confirmSourceRemoval(jobId: string): Promise<void>;
  subscribe(
    onJob: (job: ArchiveTask) => void,
    onError: (error: unknown) => void,
    onNotice?: (message: string) => void,
  ): Promise<() => void>;
}
export interface QuarantineItem {
  id: string
  mediaId: string
  name: string
  bytes: number
  sha256: string
  state: string
  createdAt: string
  updatedAt: string
  restoreViaSystemTrash: boolean
  originalPreserved: true
}
export interface LocalFileActionService {
  selectDirectory(): Promise<LocalDirectoryGrant | null>
  prepare(request:
    | { tool: 'rename_item'; mediaId: string; newName: string }
    | { tool: 'move_item'; mediaId: string; destinationGrantId: string }
    | { tool: 'cleanup_quarantine'; quarantineId: string }
  ): Promise<LocalFileActionPreview>
  confirm(actionId: string, accepted: boolean): Promise<LocalFileActionReceipt>
  stageCandidate(mediaId: string): Promise<{ id: string; name: string; state: string; originalPreserved: true }>
  listQuarantine(): Promise<QuarantineItem[]>
}
export type ModelStatus = UnconfiguredModelStatus | ConfiguredModelStatus;
export interface ModelService {
  status(): Promise<ModelStatus>;
  listLocalModels(): Promise<{ available: boolean; models: string[] }>;
  ask(query: string, identity: { sessionId: string; turnId: string }, consent: { explicitConsent: true }): Promise<AgentReply>;
  /** Explicitly resumes the same persisted model turn. Never invoked as a retry fallback. */
  resume?(query: string, identity: { sessionId: string; turnId: string }, consent: { explicitConsent: true }): Promise<AgentReply>;
}
export interface ModelProviderConfig {
  configured: boolean;
  provider: "ollama" | "dashscope" | "deepseek";
  model: string;
  secretStorage: "os_keychain";
  cloudKeyStored?: boolean;
  localModel?: string;
  cloudProvider?: "dashscope" | "deepseek";
  cloudModel?: string;
}
export interface ModelProviderConfigService {
  status(): Promise<ModelProviderConfig>;
  selectLocalModel(model: string): Promise<ModelProviderConfig>;
  activateCloudModel(): Promise<ModelProviderConfig>;
  save(provider: "dashscope" | "deepseek", model: string, apiKey: string): Promise<ModelProviderConfig>;
  remove(): Promise<ModelProviderConfig>;
}
export type AppApi = DesktopApi & {
  mode: "native" | "mock";
  local?: LocalIntegration;
  model?: ModelService;
  modelProviderConfig?: ModelProviderConfigService;
  netdisk?: NetdiskApi;
};

export interface SkippedFile { relative: string; reason: string; detail?: string | null }
export interface ScanReport { skipped: SkippedFile[]; skippedTotal: number; skippedTruncated: boolean; scanned: number }
export interface IndexResult { files: number; changed: number; skippedCount?: number; skipped?: SkippedFile[] }
export interface NetdiskProvider { id: string; display: string; approval: string; authorized: boolean; realUploadAvailable: boolean }
export interface NetdiskTask { taskId: string; provider: string; name: string; bytes: number; state: string; verifiedRemote: boolean; chunksDone: number; chunksTotal: number; error?: { code: string; message?: string } | string | null }
export interface NetdiskApi {
  providers(): Promise<NetdiskProvider[]>;
  tasks(): Promise<NetdiskTask[]>;
  beginAuth(provider: string): Promise<{ authorizeUrl: string; expiresInSeconds: number }>;
  authPoll(provider: string): Promise<{ state: string; error?: string | null }>;
  completeAuth(provider: string): Promise<void>;
  enqueue(provider: string, file: Pick<MediaFile, 'id' | 'name' | 'path'>): Promise<void>;
  run(taskId: string): Promise<void>;
  cancel(taskId: string): Promise<void>;
  unbind(provider: string): Promise<void>;
}
