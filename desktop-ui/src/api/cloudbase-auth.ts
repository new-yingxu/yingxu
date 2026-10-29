import type { UserSession } from "./contracts";
import type { CloudIdentitySession } from "./cloud-identity";

export interface CloudBaseUser {
  uid?: string;
  email?: string;
  username?: string;
  displayName?: string;
  nickname?: string;
  name?: string;
}

type CloudBaseReply = {
  data?: unknown;
  error?: { code?: string; message?: string } | null;
};
type OtpCallback = (input: { token: string }) => Promise<unknown>;
type PasswordResetCallback = (input: { nonce: string; password: string }) => Promise<unknown>;

/** CloudBase Web SDK v3 auth facade. Result envelopes are checked because SDK errors are returned, not always thrown. */
export interface CloudBaseAuthClient {
  signInWithOtp(input: { email: string; options?: { shouldCreateUser?: boolean } }): Promise<CloudBaseReply>;
  signInWithPassword(input: { email: string; password: string }): Promise<CloudBaseReply>;
  signUp(input: { email: string; password: string; nickname?: string }): Promise<CloudBaseReply>;
  resetPasswordForEmail(email: string): Promise<CloudBaseReply>;
  getSession(): Promise<CloudBaseReply>;
  getCurrentUser(): Promise<CloudBaseReply | CloudBaseUser | null>;
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

function unwrap(value: unknown, fallbackCode: string, fallbackMessage: string): unknown {
  const root = record(value);
  const error = record(root?.error);
  if (error) {
    const code = typeof error.code === "string" ? error.code : fallbackCode;
    if (code === "not_found" || code === "user_not_found") throw new DesktopAuthError("ACCOUNT_NOT_FOUND", "此邮箱尚未注册，请先创建账号。");
    if (code === "failed_precondition" && /register|exist|registered/i.test(String(error.message || ""))) {
      throw new DesktopAuthError("ACCOUNT_EXISTS", "此邮箱已注册，请返回登录。");
    }
    throw new DesktopAuthError(code, fallbackMessage);
  }
  return root && Object.hasOwn(root, "data") ? root.data : value;
}

function callbackFrom(value: unknown, key: "verifyOtp" | "updateUser"): Function {
  const payload = record(unwrap(value, "AUTH_PROVIDER_FAILED", "CloudBase 暂时不可用"));
  const callback = payload?.[key];
  if (typeof callback !== "function") throw new DesktopAuthError("INVALID_AUTH_REPLY", "CloudBase 未返回可用的邮箱验证流程，请稍后重试。");
  return callback;
}

function emailAddress(value: string): string {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new DesktopAuthError("INVALID_EMAIL", "请输入有效的邮箱地址");
  }
  return email;
}

type PendingAuth =
  | { email: string; purpose: "login"; verify: OtpCallback }
  | { email: string; purpose: "register"; verify: OtpCallback; password: string; nickname?: string }
  | { email: string; purpose: "forgot-password"; updateUser: PasswordResetCallback };

export function createCloudBaseAuthService(options: {
  auth: CloudBaseAuthClient;
  identity: CloudIdentityClient;
  readiness: AuthReadiness;
  now?: () => number;
}) {
  const now = options.now ?? Date.now;
  let pending: PendingAuth | null = null;
  let sessionView: UserSession | null = null;

  function ensureReady() {
    if (!options.readiness.ready) {
      throw new DesktopAuthError("AUTH_NOT_CONFIGURED", options.readiness.reason || "邮箱登录服务尚未完成配置");
    }
  }

  async function sessionFromIdentity(fallback: { email?: string; username?: string } = {}): Promise<UserSession> {
    const identity = await options.identity.exchange();
    let user: CloudBaseUser | null = null;
    try {
      const current = unwrap(await options.auth.getCurrentUser(), "CURRENT_USER_FAILED", "CloudBase 用户状态暂时无法读取");
      const data = record(current);
      user = (record(data?.user) ?? data) as CloudBaseUser | null;
    } catch { /* the verified identity service remains authoritative */ }
    const displayName = user?.displayName?.trim() || user?.nickname?.trim() || user?.name?.trim() || user?.username?.trim() || fallback.username?.trim() || fallback.email?.split("@")[0] || "影序用户";
    sessionView = {
      userId: identity.ownerId,
      displayName,
      email: user?.email || fallback.email || "",
      deviceId: identity.deviceId,
      expiresAt: new Date(identity.expiresAt).toISOString(),
    };
    return { ...sessionView };
  }

  function matchingPending(emailValue: string, purpose: PendingAuth["purpose"]): PendingAuth {
    const email = emailAddress(emailValue);
    if (!pending || pending.email !== email || pending.purpose !== purpose) {
      throw new DesktopAuthError("VERIFICATION_REQUIRED", "请先为当前邮箱获取验证码");
    }
    return pending;
  }

  async function verify(callback: Function, code: string, fallbackCode: string, message: string) {
    try { unwrap(await callback({ token: code }), fallbackCode, message); }
    catch (error) {
      if (error instanceof DesktopAuthError) throw error;
      throw new DesktopAuthError(fallbackCode, message);
    }
  }

  return {
    configuration(): AuthReadiness { return { ...options.readiness }; },

    async sendEmailCode(emailValue: string, purpose: PendingAuth["purpose"], registration?: { password: string; nickname?: string }): Promise<void> {
      ensureReady();
      const email = emailAddress(emailValue);
      try {
        if (purpose === "login") {
          const reply = await options.auth.signInWithOtp({ email, options: { shouldCreateUser: false } });
          pending = { email, purpose, verify: callbackFrom(reply, "verifyOtp") as OtpCallback };
        } else if (purpose === "register") {
          const password = registration?.password || "";
          if (password.length < 8 || password.length > 128) throw new DesktopAuthError("INVALID_PASSWORD", "密码长度需为 8 至 128 个字符");
          const nickname = registration?.nickname?.trim().slice(0, 40) || undefined;
          const reply = await options.auth.signUp({ email, password, ...(nickname ? { nickname } : {}) });
          pending = { email, purpose, verify: callbackFrom(reply, "verifyOtp") as OtpCallback, password, nickname };
        } else {
          const reply = await options.auth.resetPasswordForEmail(email);
          pending = { email, purpose, updateUser: callbackFrom(reply, "updateUser") as PasswordResetCallback };
        }
      } catch (error) {
        if (error instanceof DesktopAuthError) throw error;
        const message = purpose === "register" ? "注册验证码发送失败，请检查邮箱地址或稍后重试" : purpose === "forgot-password" ? "密码重置邮件发送失败，请检查邮箱地址或稍后重试" : "验证码发送失败，请检查邮箱地址或稍后重试";
        throw new DesktopAuthError("EMAIL_CODE_SEND_FAILED", message);
      }
    },

    async login(method: "wechat" | "phone-code" | "email-code" | "email-password", identifier: string, proof: string): Promise<UserSession> {
      ensureReady();
      if (method === "phone-code") throw new DesktopAuthError("SMS_NOT_CONFIGURED", "短信登录暂未开放。");
      if (method === "wechat") throw new DesktopAuthError("WECHAT_NOT_ENABLED", "微信登录当前未开放。");
      if (!identifier.trim() || !proof.trim()) throw new DesktopAuthError("MISSING_CREDENTIAL", "请填写登录信息和验证码。");
      const email = emailAddress(identifier);
      if (method === "email-code") {
        const challenge = matchingPending(email, "login") as Extract<PendingAuth, { purpose: "login" }>;
        await verify(challenge.verify, proof.trim(), "EMAIL_LOGIN_FAILED", "邮箱验证码不正确，或登录服务暂时不可用");
      } else {
        if (!options.readiness.usernamePassword) throw new DesktopAuthError("USERNAME_LOGIN_DISABLED", "邮箱密码登录尚未配置。");
        try { unwrap(await options.auth.signInWithPassword({ email, password: proof }), "PASSWORD_LOGIN_FAILED", "邮箱或密码不正确，或登录服务暂时不可用"); }
        catch (error) {
          if (error instanceof DesktopAuthError) throw error;
          throw new DesktopAuthError("PASSWORD_LOGIN_FAILED", "邮箱或密码不正确，或登录服务暂时不可用");
        }
      }
      pending = null;
      return sessionFromIdentity({ email });
    },

    async registerEmail(input: { email: string; verificationCode: string; nickname?: string; password: string }): Promise<UserSession> {
      ensureReady();
      const challenge = matchingPending(input.email, "register") as Extract<PendingAuth, { purpose: "register" }>;
      const nickname = input.nickname?.trim().slice(0, 40) || undefined;
      if (input.password !== challenge.password || nickname !== challenge.nickname) {
        throw new DesktopAuthError("REGISTRATION_CHANGED", "邮箱验证码已与当前注册信息不匹配，请重新获取验证码。");
      }
      if (!input.verificationCode.trim()) throw new DesktopAuthError("INVALID_CODE", "请输入邮箱验证码");
      await verify(challenge.verify, input.verificationCode.trim(), "EMAIL_VERIFICATION_FAILED", "邮箱验证失败，请检查验证码并重试");
      pending = null;
      return sessionFromIdentity({ email: challenge.email, username: challenge.nickname });
    },

    async resetPassword(input: { email: string; verificationCode: string; password: string }): Promise<UserSession> {
      ensureReady();
      const challenge = matchingPending(input.email, "forgot-password") as Extract<PendingAuth, { purpose: "forgot-password" }>;
      if (input.password.length < 8 || input.password.length > 128) throw new DesktopAuthError("INVALID_PASSWORD", "密码长度需为 8 至 128 个字符");
      if (!input.verificationCode.trim()) throw new DesktopAuthError("INVALID_CODE", "请输入邮箱验证码");
      try { unwrap(await challenge.updateUser({ nonce: input.verificationCode.trim(), password: input.password }), "PASSWORD_RESET_FAILED", "验证码无效或密码重置失败，请重试"); }
      catch (error) {
        if (error instanceof DesktopAuthError) throw error;
        throw new DesktopAuthError("PASSWORD_RESET_FAILED", "验证码无效或密码重置失败，请重试");
      }
      pending = null;
      return sessionFromIdentity({ email: challenge.email });
    },

    async restore(): Promise<UserSession | null> {
      if (!options.readiness.ready) return null;
      let state: unknown;
      try { state = unwrap(await options.auth.getSession(), "AUTH_RESTORE_FAILED", "CloudBase 登录状态暂时无法读取"); }
      catch { throw new DesktopAuthError("AUTH_RESTORE_FAILED", "CloudBase 登录状态暂时无法读取"); }
      const session = record(record(state)?.session);
      if (!session) return null;
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
