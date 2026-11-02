/**
 * 影序 Pocket 账号门面（CloudBase 托管）。
 * 取代旧 firebase-auth.ts：一个影序账号在桌面端与手机端通用，
 * 登录态由 CloudBase 账号令牌 + 影序设备短会话组成，凭证只存系统安全存储。
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createYingxuCloudClient, CLOUD_RUNTIME_CONFIG } from "./yingxu-cloud.cjs";
import { secureStore } from "./media-access";

export const LOGIN_SKIPPED_KEY = "yingxuLoginSkippedV1";

export async function createCloudClient() {
  const client = createYingxuCloudClient({
    secureStore,
    deviceId: await getDeviceId(),
  });
  return client;
}

export async function getDeviceId(): Promise<string> {
  const key = "yingxuPocketDeviceIdV1";
  let deviceId = await AsyncStorage.getItem(key);
  if (!deviceId) {
    deviceId = `pocket-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    await AsyncStorage.setItem(key, deviceId);
  }
  return deviceId;
}

export async function hasSkippedLogin(): Promise<boolean> {
  return (await AsyncStorage.getItem(LOGIN_SKIPPED_KEY)) === "1";
}

export async function markLoginSkipped(): Promise<void> {
  await AsyncStorage.setItem(LOGIN_SKIPPED_KEY, "1");
}

export async function clearLoginSkipped(): Promise<void> {
  await AsyncStorage.removeItem(LOGIN_SKIPPED_KEY).catch(() => {});
}

/** 云端登录是否已配置（内嵌默认值 + EXPO_PUBLIC_* 覆盖，全部就绪才可云端登录）。 */
export function cloudConfigReady(): boolean {
  return Boolean(
    CLOUD_RUNTIME_CONFIG.authOrigin
    && CLOUD_RUNTIME_CONFIG.clientId
    && CLOUD_RUNTIME_CONFIG.identityApiUrl,
  );
}
