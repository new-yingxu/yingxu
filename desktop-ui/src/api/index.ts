import { isTauri } from "@tauri-apps/api/core";
import { createMockApi } from "./mock";
import { createTauriApi } from "./tauri";
import type { AppApi, Confirmation } from "./contracts";
/** Native failures stay failures. Only a browser without Tauri gets explicit mock mode. */
export function createDesktopApi(
  confirm: (value: Confirmation) => Promise<boolean>,
  confirmModelUse: (value: Confirmation) => Promise<boolean> = confirm,
): AppApi {
  return isTauri()
    ? createTauriApi({ confirm, confirmModelUse })
    : { ...createMockApi(), mode: "mock" };
}
