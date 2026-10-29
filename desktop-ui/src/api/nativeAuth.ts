import { invoke, isTauri } from '@tauri-apps/api/core'

export interface NativeAuthIdentity {
  ownerId: string
  deviceId: string
  origin: 'desktop' | 'mobile'
  displayName: string
  expiresIn: number
  expiresAtUnix: number
  deviceAttested: false
}

export interface EmailCodeChallenge {
  verificationId: string
  expiresIn: number
}

export const isNativeTauriRuntime = (): boolean => isTauri()

export function passwordSignIn(username: string, password: string): Promise<NativeAuthIdentity> {
  if (!isTauri()) return Promise.reject(new Error('原生登录只在影序桌面端可用。'))
  return invoke<NativeAuthIdentity>('cloudbase_password_sign_in', { username, password })
}

export function requestEmailCode(email: string, forSignup: boolean): Promise<EmailCodeChallenge> {
  if (!isTauri()) return Promise.reject(new Error('原生邮箱认证只在影序桌面端可用。'))
  return invoke<EmailCodeChallenge>('cloudbase_request_email_code', { email, forSignup })
}

export function emailCodeSignIn(email: string, verificationId: string, verificationCode: string): Promise<NativeAuthIdentity> {
  if (!isTauri()) return Promise.reject(new Error('原生邮箱认证只在影序桌面端可用。'))
  return invoke<NativeAuthIdentity>('cloudbase_email_code_sign_in', { email, verificationId, verificationCode })
}

export function emailCodeSignUp(email: string, password: string, verificationId: string, verificationCode: string): Promise<NativeAuthIdentity> {
  if (!isTauri()) return Promise.reject(new Error('原生邮箱认证只在影序桌面端可用。'))
  return invoke<NativeAuthIdentity>('cloudbase_email_code_sign_up', { email, password, verificationId, verificationCode })
}

export function signOut(): Promise<void> {
  if (!isTauri()) return Promise.reject(new Error('原生退出只在影序桌面端可用。'))
  return invoke<void>('cloudbase_sign_out')
}

/** Ask CloudBase to send a short-lived verification code to the signed-in account email. */
export function requestAccountChangeCode(): Promise<void> {
  if (!isTauri()) return Promise.reject(new Error('账户安全操作仅在影序桌面端可用。'))
  return invoke<void>('cloudbase_request_account_change_code')
}

/** Update the current CloudBase account's login username through the native host. */
export function updateAccountUsername(username: string): Promise<void> {
  if (!isTauri()) return Promise.reject(new Error('账户安全操作仅在影序桌面端可用。'))
  return invoke<void>('cloudbase_update_account_username', { username })
}

/** Change the current CloudBase account password; secret values stay in the native host call. */
export function updateAccountPassword(oldPassword: string, newPassword: string, verificationCode: string): Promise<void> {
  if (!isTauri()) return Promise.reject(new Error('账户安全操作仅在影序桌面端可用。'))
  return invoke<void>('cloudbase_update_account_password', {
    oldPassword,
    newPassword,
    verificationCode,
  })
}

export function currentCloudSession(): Promise<NativeAuthIdentity | null> {
  if (!isTauri()) return Promise.resolve(null)
  return invoke<NativeAuthIdentity | null>('cloud_session_current')
}

export function cloudApiRequest<T>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  if (!isTauri()) return Promise.reject(new Error('影序云端 API 仅能通过桌面原生安全通道访问。'))
  return invoke<T>('cloud_api_request', { method, path, body: body ?? null })
}

export interface CloudSyncPreferences { syncRecords: boolean; syncAgentSessions: boolean; syncThumbnails: boolean; syncPaths: boolean }

export function getCloudSyncPreferences(): Promise<CloudSyncPreferences> {
  return cloudApiRequest('GET', '/v1/account/sync-preferences')
}

export function putCloudSyncPreferences(preferences: CloudSyncPreferences): Promise<CloudSyncPreferences> {
  return cloudApiRequest('PUT', '/v1/account/sync-preferences', preferences)
}

export async function syncBackupRecords(): Promise<{ sent: number; acknowledged: number }> {
  if (!isTauri()) return Promise.reject(new Error('备份记录同步仅在影序桌面端可用。'))
  return invoke<{ sent: number; acknowledged: number }>('cloud_sync_backup_records')
}

export interface NativeCoreReply<T> { ok: boolean; data?: T; error?: { code: string; message: string } }

export async function nativeCoreRequest<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (!isTauri()) throw new Error('本机数据库仅在影序桌面端可用。')
  const reply = await invoke<NativeCoreReply<T>>('backend_request', {
    request: { requestId: `ui-${Date.now()}`, version: 1, method, params },
  })
  if (!reply.ok) throw new Error(reply.error?.message || '本机数据库请求失败。')
  return reply.data as T
}
