export type DesktopRuntimeMode = 'native' | 'mock'

export function canEnterNativeLocalMode(mode: DesktopRuntimeMode, hasLocalAdapter: boolean): boolean {
  return mode === 'native' && hasLocalAdapter
}

export function canShowDevDiagnostics(mode: DesktopRuntimeMode, hasLocalAdapter: boolean, isDev: boolean): boolean {
  return canEnterNativeLocalMode(mode, hasLocalAdapter) && isDev
}

export function canUseCloudFeatures(session: unknown, localMode: boolean): boolean {
  return !localMode && session !== null && session !== undefined
}

export async function runCloudAction(session: unknown, localMode: boolean, action: () => Promise<void>): Promise<boolean> {
  if (!canUseCloudFeatures(session, localMode)) return false
  await action()
  return true
}

export async function enterNativeLocalMode(options: {
  mode: DesktopRuntimeMode
  hasLocalAdapter: boolean
  loadLocalData: () => Promise<void>
  setSession: (session: null) => void
  setEnabled: (enabled: true) => void
}): Promise<boolean> {
  if (!canEnterNativeLocalMode(options.mode, options.hasLocalAdapter)) return false
  options.setSession(null)
  await options.loadLocalData()
  options.setSession(null)
  options.setEnabled(true)
  return true
}
