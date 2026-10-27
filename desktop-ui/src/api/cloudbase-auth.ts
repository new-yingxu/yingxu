import type { UserSession } from "./contracts";
import type { CloudIdentitySession } from "./cloud-identity";

export interface CloudBaseUser {
  uid?: string;
  email?: string;
  username?: string;
  displayName?: string;
  name?: string;
}

export interface CloudBaseAuthClient {
  getVerification(input: { email: string }): Promise<unknown>;
  verify(input: { verification_id: string; verification_code: string }): Promise<unknown>;
  signInWithEmail(input: { verificationInfo: { verification_id: string; is_user: boolean }; verificationCode: string; email: string }): Promise<unknown>;
  signIn(input: { username: string; password: string }): Promise<unknown>;
  signUp(input: { email: string; verification_code: string; verification_token: string; username: string; password: string }): Promise<unknown>;
  getLoginState(): Promise<unknown | null>;
  getCurrentUser(): Promise<CloudBaseUser | null>;
  getAccessToken(): Promise<{ accessToken: string; env: string }>;
  signOut(): Promise<unknown>;
}

export interface CloudIdentityClient {
  exchange(): Promise<CloudIdentitySession>;
  session(): CloudIdentitySession | null;
  getUserToken(): Promise<string | null>;
  logout(): Promise<void>;
}

export interface AuthReadiness {
  ready: boolean;
  emailOtp: boolean;
  usernamePassword: boolean;
  reason: string;
}

export function cloudBaseReadiness(config: {
  envId?: string; region?: string; clientId?: string; allowedOrigins?: string;
  authReady?: string; identityApiUrl?: string; identityApiReady?: string;
}, currentOrigin: string): AuthReadiness {
  const allowedOrigins = (config.allowedOrigins || "").split(",").map((origin) => origin.trim()).filter(Boolean);
  if (!config.envId?.trim() || !config.region?.trim() || !config.clientId?.trim()) {
    return { ready: false, emailOtp: false, usernamePassword: false, reason: "CloudBase 环境、地域和公开 Client ID 尚未配置。" };
  }
  if (!allowedOrigins.includes(currentOrigin)) {
    return { ready: false, emailOtp: false, usernamePassword: false, reason: "当前桌面来源尚未加入 CloudBase Web 安全域名白名单。" };
  }
  if (config.authReady !== "true") {
    return { ready: false, emailOtp: false, usernamePassword: false, reason: "CloudBase 邮箱认证和邮件代发尚未完成控制台配置。" };
  }
  if (config.identityApiReady !== "true" || !config.identityApiUrl?.trim()) {
    return { ready: false, emailOtp: false, usernamePassword: false, reason: "影序 HTTPS 身份服务尚未配置，登录后暂不能建立桌面会话。" };
  }
  try {
    const url = new URL(config.identityApiUrl);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) throw new Error("insecure");
  } catch {
    return { ready: false, emailOtp: false, usernamePassword: false, reason: "影序身份服务地址无效或未使用 HTTPS。" };
  }
  return { ready: true, emailOtp: true, usernamePassword: true, reason: "" };
}

export class DesktopAuthError extends Error {
  constructor(public code: string, message: string) {
    super(message);
    this.name = "DesktopAuthError";
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function verificationInfo(value: unknown): { verification_id: string; is_user: boolean } {
  const root = record(value);
  const candidate = record(root?.verificationInfo) ?? record(root?.verification_info) ?? root;
  if (typeof candidate?.verification_id !== "string" || typeof candidate.is_user !== "boolean") {
    throw new DesktopAuthError("INVALID_VERIFICATION_REPLY", "邮箱验证码服务返回无效，请稍后重试");
  }
  return { verification_id: candidate.verification_id, is_user: candidate.is_user };
}

function verificationToken(value: unknown): string {
  const root = record(value);
  const token = root?.verification_token ?? record(root?.data)?.verification_token;
  if (typeof token !== "string" || !token.trim()) {
    throw new DesktopAuthError("INVALID_VERIFICATION_REPLY", "邮箱验证未通过，请检查验证码");
  }
  return token;
}

function emailAddress(value: string): string {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new DesktopAuthError("INVALID_EMAIL", "请输入有效的邮箱地址");
  }
  return email;
}

function safeFailure(code: string, message: string) {
  return new DesktopAuthError(code, message);
}

export function createCloudBaseAuthService(options: {
  auth: CloudBaseAuthClient;
  identity: CloudIdentityClient;
  readiness: AuthReadiness;
  now?: () => number;
}) {
  const now = options.now ?? Date.now;
  let pending: { email: string; purpose: "login" | "register"; info: { verification_id: string; is_user: boolean } } | null = null;
  let sessionView: UserSession | null = null;

  function ensureReady() {
    if (!options.readiness.ready) {
      throw new DesktopAuthError("AUTH_NOT_CONFIGURED", options.readiness.reason || "邮箱登录服务尚未完成配置");
    }
  }

  async function sessionFromIdentity(fallback: { email?: string; username?: string } = {}): Promise<UserSession> {
    const identity = await options.identity.exchange();
    let user: CloudBaseUser | null = null;
    try { user = await options.auth.getCurrentUser(); } catch { /* identity exchange remains authoritative */ }
    const displayName = user?.displayName?.trim() || user?.name?.trim() || user?.username?.trim() || fallback.username?.trim() || fallback.email?.split("@")[0] || "影序用户";
    sessionView = {
      userId: identity.ownerId,
      displayName,
      email: user?.email || fallback.email || "",
      deviceId: identity.deviceId,
      expiresAt: new Date(identity.expiresAt).toISOString(),
    };
    return { ...sessionView };
  }

  async function requirePending(emailValue: string, purpose: "login" | "register") {
    const email = emailAddress(emailValue);
    if (!pending || pending.email !== email || pending.purpose !== purpose) {
      throw new DesktopAuthError("VERIFICATION_REQUIRED", "请先为当前邮箱获取验证码");
    }
    if (purpose === "login" && !pending.info.is_user) {
      throw new DesktopAuthError("ACCOUNT_NOT_FOUND", "该邮箱尚未注册，请先创建账号");
    }
    if (purpose === "register" && pending.info.is_user) {
      throw new DesktopAuthError("ACCOUNT_EXISTS", "该邮箱已注册，请直接登录");
    }
    return { email, info: pending.info };
  }

  return {
    configuration(): AuthReadiness { return { ...options.readiness }; },

    async sendEmailCode(emailValue: string, purpose: "login" | "register"): Promise<void> {
      ensureReady();
      const email = emailAddress(emailValue);
      let response: unknown;
      try { response = await options.auth.getVerification({ email }); }
      catch { throw safeFailure("EMAIL_CODE_SEND_FAILED", "验证码发送失败，请检查邮箱地址或稍后重试"); }
      const info = verificationInfo(response);
      if (purpose === "login" && !info.is_user) throw new DesktopAuthError("ACCOUNT_NOT_FOUND", "该邮箱尚未注册，请先创建账号");
      if (purpose === "register" && info.is_user) throw new DesktopAuthError("ACCOUNT_EXISTS", "该邮箱已注册，请直接登录");
      pending = { email, purpose, info };
    },

    async login(method: "wechat" | "phone-code" | "email-code" | "email-password", identifier: string, proof: string): Promise<UserSession> {
      ensureReady();
      if (method === "phone-code") throw new DesktopAuthError("SMS_NOT_CONFIGURED", "短信通道尚未完成服务商、签名和模板配置，暂不能发送或验证短信。");
      if (method === "wechat") throw new DesktopAuthError("WECHAT_NOT_ENABLED", "微信登录当前未开放。");
      if (!identifier.trim() || !proof.trim()) throw new DesktopAuthError("MISSING_CREDENTIAL", "请填写登录信息和验证码。");
      if (method === "email-code") {
        const { email, info } = await requirePending(identifier, "login");
        try { await options.auth.signInWithEmail({ verificationInfo: info, verificationCode: proof.trim(), email }); }
        catch { throw safeFailure("EMAIL_LOGIN_FAILED", "邮箱验证码不正确，或登录服务暂时不可用"); }
        pending = null;
        return sessionFromIdentity({ email });
      }
      if (!options.readiness.usernamePassword) throw new DesktopAuthError("USERNAME_LOGIN_DISABLED", "用户名和密码登录尚未配置。");
      try { await options.auth.signIn({ username: identifier.trim(), password: proof }); }
      catch { throw safeFailure("PASSWORD_LOGIN_FAILED", "用户名或密码不正确，或登录服务暂时不可用"); }
      pending = null;
      return sessionFromIdentity({ username: identifier.trim() });
    },

    async registerEmail(input: { email: string; verificationCode: string; username: string; password: string }): Promise<UserSession> {
      ensureReady();
      const { email, info } = await requirePending(input.email, "register");
      const username = input.username.trim();
      if (!/^(?=.{1,32}$)(?=.*[A-Za-z])[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?$/.test(username)) {
        throw new DesktopAuthError("INVALID_USERNAME", "用户名需为 1 至 32 位字母、数字、下划线或连字符，且不能是纯数字或以符号开头/结尾");
      }
      if (input.password.length < 8 || input.password.length > 128) throw new DesktopAuthError("INVALID_PASSWORD", "密码长度需为 8 至 128 个字符");
      if (!input.verificationCode.trim()) throw new DesktopAuthError("INVALID_CODE", "请输入邮箱验证码");
      let verified: unknown;
      try { verified = await options.auth.verify({ verification_id: info.verification_id, verification_code: input.verificationCode.trim() }); }
      catch { throw safeFailure("EMAIL_VERIFICATION_FAILED", "邮箱验证失败，请检查验证码并重试"); }
      const token = verificationToken(verified);
      try { await options.auth.signUp({ email, verification_code: input.verificationCode.trim(), verification_token: token, username, password: input.password }); }
      catch { throw safeFailure("SIGNUP_FAILED", "账号创建失败，请检查用户名、密码或稍后重试"); }
      pending = null;
      return sessionFromIdentity({ email, username });
    },

    async restore(): Promise<UserSession | null> {
      if (!options.readiness.ready) return null;
      let loginState: unknown | null;
      try { loginState = await options.auth.getLoginState(); }
      catch { throw safeFailure("AUTH_RESTORE_FAILED", "CloudBase 登录状态暂时无法读取"); }
      if (!loginState) return null;
      const existing = options.identity.session();
      if (existing && existing.expiresAt > now() && sessionView) return { ...sessionView };
      return sessionFromIdentity();
    },

    async logout(): Promise<void> {
      sessionView = null;
      pending = null;
      let identityError: unknown;
      try { await options.identity.logout(); } catch (error) { identityError = error; }
      try { await options.auth.signOut(); }
      catch {
        if (identityError) throw identityError;
        throw new DesktopAuthError("CLOUD_SIGNOUT_FAILED", "本机短会话已清除，但 CloudBase 退出登录未完成");
      }
      if (identityError) throw identityError;
    },
  };
}
