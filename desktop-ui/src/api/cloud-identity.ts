/**
 * Ephemeral desktop identity exchange for the CloudBase account session.
 * The account token and short app token live only in this closure; callers can
 * provide the app token to createCloudClient without writing browser storage.
 */
export interface CloudIdentitySession {
  ownerId: string
  deviceId: string
  origin: 'desktop'
  deviceAttested: false
  expiresAt: number
}

export class CloudIdentityError extends Error {
  constructor(public code: string, message: string, public status = 0) { super(message) }
}

type ExchangeReply = {
  accessToken: string
  tokenType: 'Bearer'
  expiresIn: 900
  ownerId: string
  deviceId: string
  origin: 'desktop'
  deviceAttested: false
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function urlFor(baseUrl: string): string {
  let url: URL
  try { url = new URL(baseUrl) } catch { throw new CloudIdentityError('INVALID_IDENTITY_URL', '身份服务地址无效') }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.username || url.password || url.search || url.hash || (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))) {
    throw new CloudIdentityError('INSECURE_IDENTITY_URL', '身份服务必须使用 HTTPS；本地测试仅允许 loopback HTTP')
  }
  return url.toString().replace(/\/$/, '')
}

function parseReply(value: unknown): ExchangeReply {
  if (!isRecord(value) || typeof value.accessToken !== 'string' || value.accessToken.length < 40 || value.accessToken.length > 16384
    || value.tokenType !== 'Bearer' || value.expiresIn !== 900
    || typeof value.ownerId !== 'string' || !value.ownerId.trim() || value.ownerId.length > 160
    || typeof value.deviceId !== 'string' || !value.deviceId.trim() || value.deviceId.length > 500
    || value.origin !== 'desktop' || value.deviceAttested !== false) {
    throw new CloudIdentityError('INVALID_IDENTITY_RESPONSE', '身份服务返回的数据不符合短会话协议')
  }
  return value as ExchangeReply
}

async function errorCode(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (isRecord(body) && isRecord(body.detail) && typeof body.detail.code === 'string') return body.detail.code.slice(0, 80)
  } catch { /* sanitized fallback below */ }
  return `HTTP_${response.status}`
}

export function createCloudBaseIdentityClient(options: {
  baseUrl: string
  displayName: string
  getCloudBaseAccountToken: () => Promise<string | null>
  fetch?: typeof fetch
  now?: () => number
  timeoutMs?: number
}) {
  const baseUrl = urlFor(options.baseUrl)
  const displayName = options.displayName.trim()
  if (!displayName || displayName.length > 120) throw new CloudIdentityError('INVALID_DEVICE_NAME', '设备名称需为 1 至 120 字')
  const fetchImpl = options.fetch ?? fetch
  const now = options.now ?? Date.now
  const timeoutMs = options.timeoutMs ?? 10000
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60000) throw new CloudIdentityError('INVALID_TIMEOUT', '身份请求超时时间无效')
  let accessToken: string | null = null
  let sessionView: CloudIdentitySession | null = null
  let expiresAtMs = 0

  function clear() {
    accessToken = null
    sessionView = null
    expiresAtMs = 0
  }
  function activeToken(): string | null {
    if (accessToken && now() >= expiresAtMs) clear()
    return accessToken
  }
  async function request(path: string, token: string, body?: Record<string, unknown>, extraHeaders?: Record<string, string>) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      return await fetchImpl(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...extraHeaders },
        ...(body ? { body: JSON.stringify(body) } : {}),
        credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal,
      })
    } catch {
      throw new CloudIdentityError('IDENTITY_UNREACHABLE', '身份服务暂时不可用；未保存本地会话')
    } finally { clearTimeout(timer) }
  }

  return {
    session(): CloudIdentitySession | null {
      if (!activeToken()) return null
      return sessionView ? { ...sessionView } : null
    },
    async getUserToken(): Promise<string | null> { return activeToken() },
    async exchange(): Promise<CloudIdentitySession> {
      if (activeToken()) throw new CloudIdentityError('SESSION_ALREADY_ACTIVE', '请先退出当前设备会话，再切换账号')
      clear()
      let accountToken: string | null
      try { accountToken = await options.getCloudBaseAccountToken() } catch { throw new CloudIdentityError('ACCOUNT_SESSION_UNAVAILABLE', '未取得有效的 CloudBase 账号会话') }
      if (typeof accountToken !== 'string' || !accountToken.trim() || accountToken.length > 16384 || /[\r\n]/.test(accountToken)) {
        throw new CloudIdentityError('ACCOUNT_SESSION_REQUIRED', '请先登录 CloudBase 账号')
      }
      const response = await request('/v1/auth/session/exchange', accountToken, { displayName })
      if (!response.ok) throw new CloudIdentityError(await errorCode(response), '身份服务拒绝建立本机短会话', response.status)
      let reply: ExchangeReply
      try { reply = parseReply(await response.json()) } catch (error) {
        clear()
        if (error instanceof CloudIdentityError) throw error
        throw new CloudIdentityError('INVALID_IDENTITY_RESPONSE', '身份服务返回的数据无法读取')
      }
      expiresAtMs = now() + reply.expiresIn * 1000
      accessToken = reply.accessToken
      sessionView = { ownerId: reply.ownerId, deviceId: reply.deviceId, origin: reply.origin, deviceAttested: false, expiresAt: expiresAtMs }
      return { ...sessionView }
    },
    /** Explicit account reauthentication only. Never called by getUserToken or API requests. */
    async renew(): Promise<CloudIdentitySession> {
      const previousToken = activeToken()
      if (!previousToken || !sessionView) throw new CloudIdentityError('SESSION_EXPIRED', '本机短会话已过期，请重新登录账号')
      let accountToken: string | null
      try { accountToken = await options.getCloudBaseAccountToken() } catch { throw new CloudIdentityError('ACCOUNT_SESSION_UNAVAILABLE', '请重新登录 CloudBase 账号后再续期') }
      if (typeof accountToken !== 'string' || !accountToken.trim() || accountToken.length > 16384 || /[\r\n]/.test(accountToken)) {
        throw new CloudIdentityError('ACCOUNT_SESSION_REQUIRED', '请重新登录 CloudBase 账号后再续期')
      }
      const previous = sessionView
      const response = await request('/v1/auth/session/renew', accountToken, {}, { 'X-Yingxu-Session': previousToken })
      if (!response.ok) {
        const code = await errorCode(response)
        if (response.status === 401 || response.status === 403) clear()
        throw new CloudIdentityError(code, response.status === 503
          ? '身份服务暂不可用；现有短会话按原到期时间有效'
          : '账号重新认证未通过，请核对账号并重新登录', response.status)
      }
      let reply: ExchangeReply
      try { reply = parseReply(await response.json()) } catch (error) {
        clear()
        if (error instanceof CloudIdentityError) throw error
        throw new CloudIdentityError('INVALID_IDENTITY_RESPONSE', '续期响应无法读取，本地会话已清除')
      }
      if (reply.ownerId !== previous.ownerId || reply.deviceId !== previous.deviceId || reply.origin !== previous.origin) {
        clear()
        throw new CloudIdentityError('IDENTITY_CHANGED', '续期身份与当前设备不一致，请重新登录')
      }
      expiresAtMs = now() + reply.expiresIn * 1000
      accessToken = reply.accessToken
      sessionView = { ownerId: reply.ownerId, deviceId: reply.deviceId, origin: reply.origin, deviceAttested: false, expiresAt: expiresAtMs }
      return { ...sessionView }
    },
    async logout(): Promise<void> {
      const token = activeToken()
      clear()
      if (!token) return
      const response = await request('/v1/auth/logout', token)
      if (!response.ok) throw new CloudIdentityError(await errorCode(response), '本地会话已清除，但服务端设备撤销未确认', response.status)
      let body: unknown
      try { body = await response.json() } catch { throw new CloudIdentityError('LOGOUT_NOT_CONFIRMED', '本地会话已清除，但服务端未确认设备撤销', response.status) }
      if (!isRecord(body) || body.loggedOut !== true || body.deviceRevoked !== true) {
        throw new CloudIdentityError('LOGOUT_NOT_CONFIRMED', '本地会话已清除，但服务端未确认设备撤销', response.status)
      }
    },
  }
}
