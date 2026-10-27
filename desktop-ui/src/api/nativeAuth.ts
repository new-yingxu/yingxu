import { invoke, isTauri } from '@tauri-apps/api/core'

export interface NativeAuthIdentity {
  subject: string
  expiresIn: number
}

export const isNativeTauriRuntime = (): boolean => isTauri()

export function passwordSignIn(username: string, password: string): Promise<NativeAuthIdentity> {
  if (!isTauri()) return Promise.reject(new Error('原生登录只在影序桌面端可用。'))
  return invoke<NativeAuthIdentity>('cloudbase_password_sign_in', { username, password })
}

export function signOut(): Promise<void> {
  if (!isTauri()) return Promise.reject(new Error('原生退出只在影序桌面端可用。'))
  return invoke<void>('cloudbase_sign_out')
}
