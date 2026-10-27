import { invoke, isTauri } from '@tauri-apps/api/core'

export interface NativeAuthIdentity {
  subject: string
  expiresIn: number
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
