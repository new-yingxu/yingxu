import type { UserSession } from "./contracts";
import {
  currentCloudSession,
  emailCodeSignIn,
  emailCodeSignUp,
  isNativeTauriRuntime,
  passwordSignIn,
  requestEmailCode,
  signOut,
  type NativeAuthIdentity,
} from "./nativeAuth";

/**
 * 原生登录通道：账号密码 / 邮箱验证码都由 Rust 宿主直连 CloudBase，
 * WebView 不参与网络请求，因此不受 CloudBase Web 安全域名与 CSP 限制。
 * 旧版能登录靠的就是这条通道；这里把它接回当前的前端接口。
 */
function toSession(identity: NativeAuthIdentity, email: string): UserSession {
  return {
    userId: identity.ownerId,
    displayName: identity.displayName?.trim() || email.split("@")[0] || "影序用户",
    email,
    deviceId: identity.deviceId,
    expiresAt: new Date(identity.expiresAtUnix * 1000).toISOString(),
  };
}

export function createNativeAuthService() {
  let pending: { email: string; verificationId: string } | null = null;
  let lastEmail = "";

  const normalized = (value: string) => value.trim().toLowerCase();

  return {
    configuration() {
      // 原生通道的可用性在编译期就定了：宿主没带 CloudBase 配置会直接报错，
      // 所以这里只要运行在 Tauri 里就视为可登录。
      return {
        ready: isNativeTauriRuntime(),
        emailOtp: isNativeTauriRuntime(),
        usernamePassword: isNativeTauriRuntime(),
        reason: isNativeTauriRuntime() ? "" : "浏览器预览无法登录影序账号。",
      };
    },

    async sendEmailCode(
      email: string,
      purpose: "login" | "register" | "forgot-password",
      _registration?: { password: string; nickname?: string },
    ): Promise<void> {
      const address = email.trim();
      if (!address) throw new Error("请先填写邮箱地址。");
      const challenge = await requestEmailCode(address, purpose === "register");
      pending = { email: normalized(address), verificationId: challenge.verificationId };
    },

    async login(
      method: "wechat" | "phone-code" | "email-code" | "email-password",
      identifier: string,
      proof: string,
    ): Promise<UserSession> {
      const address = identifier.trim();
      if (method === "email-password") {
        const identity = await passwordSignIn(address, proof);
        lastEmail = address;
        return toSession(identity, address);
      }
      if (method === "email-code") {
        if (!pending || pending.email !== normalized(address)) {
          throw new Error("请先为当前邮箱获取验证码。");
        }
        const identity = await emailCodeSignIn(address, pending.verificationId, proof.trim());
        pending = null;
        lastEmail = address;
        return toSession(identity, address);
      }
      throw new Error("当前版本仅支持邮箱密码或邮箱验证码登录。");
    },

    async registerEmail(input: {
      email: string;
      verificationCode: string;
      nickname?: string;
      password: string;
    }): Promise<UserSession> {
      const address = input.email.trim();
      if (!pending || pending.email !== normalized(address)) {
        throw new Error("请先为当前邮箱获取验证码。");
      }
      const identity = await emailCodeSignUp(
        address,
        input.password,
        pending.verificationId,
        input.verificationCode.trim(),
      );
      pending = null;
      lastEmail = address;
      return toSession(identity, address);
    },

    async resetPassword(_input: {
      email: string;
      verificationCode: string;
      password: string;
    }): Promise<UserSession> {
      throw new Error("当前版本暂不支持邮箱重置密码，请直接用邮箱验证码登录。");
    },

    async restore(): Promise<UserSession | null> {
      const identity = await currentCloudSession();
      return identity ? toSession(identity, lastEmail) : null;
    },

    async logout(): Promise<void> {
      await signOut();
    },
  };
}
