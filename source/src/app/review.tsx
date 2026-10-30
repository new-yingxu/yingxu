import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { mediaAccess, type MediaFile } from "../services/media-access";
import { loadApiKey, reviewPhoto, saveApiKey, type PhotoReview } from "../services/ai-review";

const PHOTO_PATTERN = /\.(jpe?g|png|webp)$/i;
const MAX_PHOTOS = 60;

const VERDICT_LABEL: Record<PhotoReview["verdict"], string> = {
  keep: "可留用",
  review: "需注意",
  reject: "建议重拍或删除",
};

const VERDICT_COLOR: Record<PhotoReview["verdict"], string> = {
  keep: "#27804A",
  review: "#A87B23",
  reject: "#A33A32",
};

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export default function ReviewScreen() {
  const [folderName, setFolderName] = useState<string | null>(null);
  const [photos, setPhotos] = useState<MediaFile[]>([]);
  const [selectedUri, setSelectedUri] = useState<string | null>(null);
  const [review, setReview] = useState<PhotoReview | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [editingKey, setEditingKey] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadApiKey()
      .then((value) => setApiKey(value ?? ""))
      .catch(() => setApiKey(""));
  }, []);

  const selected = useMemo(
    () => photos.find((photo) => photo.uri === selectedUri) ?? null,
    [photos, selectedUri],
  );

  const pickFolder = useCallback(async () => {
    setError(null);
    setReview(null);
    try {
      const folder = await mediaAccess.pickDirectoryAsync();
      if (!folder) return;

      setScanning(true);
      setFolderName(folder.name);
      const files = await mediaAccess.scanMediaAsync(folder.uri);
      const photoFiles = files
        .filter((file) => file.mediaType === "photo" && PHOTO_PATTERN.test(file.name))
        .slice(0, MAX_PHOTOS);

      setPhotos(photoFiles);
      setSelectedUri(photoFiles[0]?.uri ?? null);

      if (photoFiles.length === 0) {
        setError("这个文件夹里没有找到可审的照片。RAW 暂不支持，请选 JPEG/PNG。");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setScanning(false);
    }
  }, []);

  const runReview = useCallback(async () => {
    if (!selected) {
      setError("先选一张照片");
      return;
    }
    if (!apiKey.trim()) {
      setEditingKey(true);
      setError("还没有 API Key，点右上角填一个");
      return;
    }

    setReviewing(true);
    setError(null);
    setReview(null);
    try {
      const result = await reviewPhoto({ uri: selected.uri, apiKey });
      setReview(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReviewing(false);
    }
  }, [selected, apiKey]);

  const persistKey = useCallback(async () => {
    try {
      await saveApiKey(apiKey);
      setEditingKey(false);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [apiKey]);

  return (
    <SafeAreaView style={styles.safeArea} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} style={styles.backButton} hitSlop={8}>
          <Text style={styles.backText}>‹ 返回</Text>
        </Pressable>
        <Text style={styles.title}>AI 审片</Text>
        <Pressable onPress={() => setEditingKey((value) => !value)} style={styles.keyButton} hitSlop={8}>
          <Text style={styles.keyButtonText}>{apiKey ? "已配置 Key" : "填 API Key"}</Text>
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {editingKey && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>API Key</Text>
            <Text style={styles.hint}>
              填一次即可，保存在系统安全存储里（Keystore / AES），不会写进代码或日志。默认走 OpenAI 兼容接口。
            </Text>
            <TextInput
              value={apiKey}
              onChangeText={setApiKey}
              placeholder="sk-..."
              placeholderTextColor="#99958E"
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              style={styles.input}
            />
            <View style={styles.actionRow}>
              <Pressable onPress={persistKey} style={styles.primaryButton}>
                <Text style={styles.primaryButtonText}>保存 Key</Text>
              </Pressable>
              <Pressable onPress={() => setEditingKey(false)} style={styles.secondaryButton}>
                <Text style={styles.secondaryButtonText}>收起</Text>
              </Pressable>
            </View>
          </View>
        )}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>选择素材</Text>
          <Text style={styles.sectionMeta}>{photos.length > 0 ? `${photos.length} 张可选` : "未选择"}</Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.bodyText}>
            从 SD 卡或手机文件夹里选一个目录，AI 会逐张看照片，给出「可留用 / 需注意 / 建议重拍」的判断与原因。
          </Text>
          <Pressable onPress={pickFolder} style={styles.secondaryButton} disabled={scanning}>
            {scanning ? (
              <ActivityIndicator />
            ) : (
              <Text style={styles.secondaryButtonText}>{folderName ? `重新选择（当前：${folderName}）` : "选择素材目录"}</Text>
            )}
          </Pressable>

          {photos.length > 0 && (
            <ScrollView style={styles.photoList} nestedScrollEnabled>
              {photos.map((photo) => {
                const active = photo.uri === selectedUri;
                return (
                  <Pressable
                    key={photo.uri}
                    onPress={() => {
                      setSelectedUri(photo.uri);
                      setReview(null);
                      setError(null);
                    }}
                    style={[styles.photoRow, active && styles.photoRowActive]}
                  >
                    <View style={styles.photoMain}>
                      <Text style={[styles.photoName, active && styles.photoNameActive]} numberOfLines={1}>
                        {photo.name}
                      </Text>
                      <Text style={styles.photoMeta}>{formatBytes(Number(photo.size || 0))}</Text>
                    </View>
                    {active && <Text style={styles.photoBadge}>已选</Text>}
                  </Pressable>
                );
              })}
            </ScrollView>
          )}

          <Pressable
            onPress={runReview}
            disabled={reviewing || !selected}
            style={[styles.primaryButton, (reviewing || !selected) && styles.disabled]}
          >
            {reviewing ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryButtonText}>让 AI 审这张</Text>
            )}
          </Pressable>

          {!!error && <Text style={styles.errorText}>{error}</Text>}
        </View>

        {!!review && (
          <View style={styles.card}>
            <View style={styles.reviewHeader}>
              <View style={[styles.verdictPill, { borderColor: VERDICT_COLOR[review.verdict] }]}>
                <Text style={[styles.verdictText, { color: VERDICT_COLOR[review.verdict] }]}>
                  {VERDICT_LABEL[review.verdict]}
                </Text>
              </View>
              <Text style={styles.scoreText}>
                {review.score > 0 ? `${review.score} 分` : "未评分"}
              </Text>
            </View>

            <Text style={styles.reviewSubject}>{review.subject || "（模型没有给出画面描述）"}</Text>

            {review.issues.length > 0 && (
              <View style={styles.issueList}>
                {review.issues.map((issue, index) => (
                  <Text key={`${index}-${issue}`} style={styles.issueText}>
                    · {issue}
                  </Text>
                ))}
              </View>
            )}

            {!!review.advice && <Text style={styles.adviceText}>{review.advice}</Text>}
          </View>
        )}

        <Text style={styles.footerHint}>
          AI 判断只是参考，最终留用与删除由你决定。审片只读取照片，不会修改或删除原文件。
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: "#F4F2ED" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#D9D5CC",
  },
  backButton: { minWidth: 64 },
  backText: { color: "#273D31", fontSize: 16 },
  title: { color: "#1E2420", fontSize: 17, fontWeight: "600" },
  keyButton: { minWidth: 64, alignItems: "flex-end" },
  keyButtonText: { color: "#273D31", fontSize: 13 },
  content: { padding: 16, gap: 12, paddingBottom: 32 },
  sectionHeader: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  sectionTitle: { color: "#1E2420", fontSize: 16, fontWeight: "600" },
  sectionMeta: { color: "#7A756C", fontSize: 12 },
  card: {
    backgroundColor: "#FFFDF8",
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "#E2DED4",
    padding: 16,
    gap: 10,
  },
  cardTitle: { color: "#1E2420", fontSize: 15, fontWeight: "600" },
  bodyText: { color: "#4A4640", fontSize: 14, lineHeight: 21 },
  hint: { color: "#7A756C", fontSize: 12, lineHeight: 18 },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "#CFCABF",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: "#1E2420",
    backgroundColor: "#FFFFFF",
  },
  actionRow: { flexDirection: "row", gap: 10 },
  primaryButton: {
    flex: 1,
    backgroundColor: "#273D31",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryButtonText: { color: "#FFFFFF", fontSize: 15, fontWeight: "600" },
  secondaryButton: {
    flex: 1,
    backgroundColor: "#EFEBE2",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  secondaryButtonText: { color: "#3A4A3F", fontSize: 14, fontWeight: "500" },
  disabled: { opacity: 0.45 },
  photoList: { maxHeight: 260 },
  photoRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 9,
    paddingHorizontal: 10,
    borderRadius: 10,
  },
  photoRowActive: { backgroundColor: "#E7EDE6" },
  photoMain: { flex: 1, paddingRight: 10 },
  photoName: { color: "#2B2A26", fontSize: 13 },
  photoNameActive: { color: "#273D31", fontWeight: "600" },
  photoMeta: { color: "#8A857B", fontSize: 11, marginTop: 2 },
  photoBadge: { color: "#273D31", fontSize: 11, fontWeight: "600" },
  reviewHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  verdictPill: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5 },
  verdictText: { fontSize: 13, fontWeight: "600" },
  scoreText: { color: "#1E2420", fontSize: 18, fontWeight: "700" },
  reviewSubject: { color: "#1E2420", fontSize: 15, lineHeight: 22 },
  issueList: { gap: 4 },
  issueText: { color: "#4A4640", fontSize: 13, lineHeight: 19 },
  adviceText: { color: "#273D31", fontSize: 14, lineHeight: 21, fontWeight: "500" },
  errorText: { color: "#A33A32", fontSize: 13, lineHeight: 19 },
  footerHint: { color: "#8A857B", fontSize: 11, lineHeight: 17, paddingHorizontal: 4 },
});
