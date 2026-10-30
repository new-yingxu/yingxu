import AsyncStorage from "@react-native-async-storage/async-storage";
import { FirebaseSyncClient } from "./firebase-sync.cjs";
import { secureStore } from "./media-access";

export const DEVICE_KEY = "yingxuFirebaseDeviceIdV1";
export const LOGIN_SKIPPED_KEY = "yingxuLoginSkippedV1";

export const FIREBASE_CONFIG = {
  apiKey: "AIzaSyA0NzN1SVbyNT2xi1HoDNn9hZQouknk-qA",
  databaseUrl: "https://yingxu-73c7b-default-rtdb.asia-southeast1.firebasedatabase.app",
};

export async function getDeviceId(): Promise<string> {
  let deviceId = await AsyncStorage.getItem(DEVICE_KEY);
  if (!deviceId) {
    deviceId = `pocket-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    await AsyncStorage.setItem(DEVICE_KEY, deviceId);
  }
  return deviceId;
}

export function makeFirebaseClient(deviceId: string) {
  return new FirebaseSyncClient({ ...FIREBASE_CONFIG, secureStore, deviceId });
}

export async function createCloudClient() {
  return makeFirebaseClient(await getDeviceId());
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
