import { requireNativeModule } from "expo-modules-core";

export type MediaFile = {
  uri: string;
  name: string;
  relativePath?: string;
  size: number;
  modifiedAt: number;
  capturedAt?: number | null;
  mediaType: "photo" | "video";
};

type YingxuMediaAccess = {
  pickDirectoryAsync(): Promise<{ uri: string; name: string } | null>;
  documentsDirectoryAsync(): Promise<string>;
  startBackupForeground(title: string, message: string): Promise<void>;
  stopBackupForeground(): Promise<void>;
  requestNotificationPermissionAsync(): Promise<boolean>;
  scanMediaAsync(uri: string): Promise<MediaFile[]>;
  readFileChunkBase64Async(sourceUri: string, offset: number, length: number): Promise<{ data: string; bytesRead: number }>;
  readThumbnailBase64Async(sourceUri: string, maxDimension: number, quality: number): Promise<{ data: string; width: number; height: number; mimeType: string }>;
  sha256Async(sourceUri: string): Promise<string>;
  copyFileVerifiedAsync(sourceUri: string, destinationUri: string, relativePath: string): Promise<{
    status: "copied" | "skipped";
    sha256: string;
    size: number;
  }>;
  beginIncomingWriteAsync(destinationRoot: string, relativePath: string, size: number, expectedHash: string): Promise<{
    status: "receiving" | "skipped";
    transferId?: string;
    sha256?: string;
    size?: number;
  }>;
  appendIncomingChunkAsync(transferId: string, base64Data: string): Promise<{ received: number }>;
  finishIncomingWriteAsync(transferId: string): Promise<{ status: "copied"; sha256: string; size: number }>;
  abortIncomingWriteAsync(transferId: string): Promise<void>;
  secureSet(key: string, value: string): Promise<void>;
  secureGet(key: string): Promise<string | null>;
  secureRemove(key: string): Promise<void>;
};

const native = requireNativeModule<YingxuMediaAccess>("YingxuMediaAccess");

export const mediaAccess = native;
export const secureStore = {
  set: (key: string, value: string) => native.secureSet(key, value),
  get: (key: string) => native.secureGet(key),
  remove: (key: string) => native.secureRemove(key),
};
