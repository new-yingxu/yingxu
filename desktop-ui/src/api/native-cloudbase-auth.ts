import { createCloudBaseIdentityClient } from "./cloud-identity";
import { cloudBaseReadiness, createCloudBaseAuthService, type AuthReadiness, type CloudBaseAuthClient } from "./cloudbase-auth";

type PublicConfig = {
  envId?: string;
  region?: string;
  clientId?: string;
  allowedOrigins?: string;
  authReady?: string;
  identityApiUrl?: string;
  identityApiReady?: string;
};

const configFromEnvironment: PublicConfig = {
  envId: import.meta.env.VITE_CLOUDBASE_ENV_ID,
  region: import.meta.env.VITE_CLOUDBASE_REGION,
  clientId: import.meta.env.VITE_CLOUDBASE_CLIENT_ID,
  allowedOrigins: import.meta.env.VITE_CLOUDBASE_ALLOWED_ORIGINS,
  authReady: import.meta.env.VITE_CLOUDBASE_AUTH_READY,
  identityApiUrl: import.meta.env.VITE_IDENTITY_API_BASE_URL,
  identityApiReady: import.meta.env.VITE_IDENTITY_API_READY,
};

export function createNativeCloudBaseAuth(config = configFromEnvironment, origin = globalThis.location?.origin ?? "") {
  const readiness = cloudBaseReadiness(config, origin);
  let sdkAuthPromise: Promise<CloudBaseAuthClient> | null = null;
  const getAuth = (): Promise<CloudBaseAuthClient> => {
    if (!readiness.ready) return Promise.reject(new Error(readiness.reason));
    if (!sdkAuthPromise) {
      sdkAuthPromise = import("@cloudbase/js-sdk").then((module) =>
        module.default.init({ env: config.envId!.trim(), region: config.region!.trim(), clientId: config.clientId!.trim(), persistence: "local" }).auth() as CloudBaseAuthClient,
      );
    }
    return sdkAuthPromise;
  };
  const auth: CloudBaseAuthClient = {
    signInWithOtp: async (input) => (await getAuth()).signInWithOtp(input),
    signInWithPassword: async (input) => (await getAuth()).signInWithPassword(input),
    signUp: async (input) => (await getAuth()).signUp(input),
    resetPasswordForEmail: async (email) => (await getAuth()).resetPasswordForEmail(email),
    getSession: async () => (await getAuth()).getSession(),
    getCurrentUser: async () => (await getAuth()).getCurrentUser(),
    getAccessToken: async () => (await getAuth()).getAccessToken(),
    signOut: async () => (await getAuth()).signOut(),
  };
  const identity = readiness.ready && config.identityApiUrl
    ? createCloudBaseIdentityClient({
      baseUrl: config.identityApiUrl,
      displayName: "影序桌面端",
      getCloudBaseAccountToken: async () => (await auth.getAccessToken()).accessToken,
    })
    : {
      async exchange() { throw new Error(readiness.reason || "身份服务尚未配置"); },
      session() { return null; },
      async getUserToken() { return null; },
      async logout() {},
    };
  return createCloudBaseAuthService({ auth, identity, readiness });
}
