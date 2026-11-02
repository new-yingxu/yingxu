import { router } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  clearLoginSkipped,
  createCloudClient,
  markLoginSkipped,
} from "../services/firebase-auth";

type Mode = "signin" | "signup";

export default function LoginScreen() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [existingUser, setExistingUser] = useState("");

  useEffect(() => {
    let cancelled = false;
    createCloudClient()
      .then(async (client) => {
        const restored = await client.restoreSession().catch(() => false);
        if (cancelled || !restored) return;
        setExistingUser(client.session?.displayName || client.session?.email || "已登录");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(nextMode: Mode = mode) {
    setMessage("");
    const trimmedEmail = email.trim();
    const trimmedName = displayName.trim();
    if (!trimmedEmail || !password) {
      setMessage("请填写邮箱和密码。");
      return;
    }
    if (nextMode === "signup" && !trimmedName) {
      setMessage("注册时请填写用户名。");
      return;
    }
    setBusy(true);
    try {
      const client = await createCloudClient();
      await client.signIn(trimmedEmail, password, nextMode === "signup", trimmedName);
      await clearLoginSkipped();
      setPassword("");
      router.replace("/");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "登录失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function skip() {
    await markLoginSkipped().catch(() => {});
    router.replace("/");
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.brandBlock}>
            <Text style={styles.eyebrow}>影序 · 账号</Text>
            <Text style={styles.brand}>Pocket</Text>
            <Text style={styles.subtitle}>
              登录后可在多台设备间同步备份进度与历史元数据。不登录也能使用手机本机备份。
            </Text>
          </View>

          {existingUser ? (
            <View style={styles.noticeCard}>
              <Text style={styles.noticeTitle}>当前已登录</Text>
              <Text style={styles.noticeBody}>{existingUser}</Text>
              <Pressable
                onPress={() => router.replace("/")}
                style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}
              >
                <Text style={styles.primaryButtonText}>进入影序 Pocket</Text>
              </Pressable>
            </View>
          ) : null}

          <View style={styles.card}>
            <View style={styles.modeRow}>
              <Pressable
                onPress={() => {
                  setMode("signin");
                  setMessage("");
                }}
                style={[styles.modeTab, mode === "signin" && styles.modeTabActive]}
              >
                <Text style={[styles.modeText, mode === "signin" && styles.modeTextActive]}>登录</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  setMode("signup");
                  setMessage("");
                }}
                style={[styles.modeTab, mode === "signup" && styles.modeTabActive]}
              >
                <Text style={[styles.modeText, mode === "signup" && styles.modeTextActive]}>注册</Text>
              </Pressable>
            </View>

            <Text style={styles.fieldLabel}>邮箱</Text>
            <TextInput
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              placeholderTextColor="#A8A49C"
              style={styles.input}
              accessibilityLabel="邮箱"
            />

            <Text style={styles.fieldLabel}>用户名{mode === "signup" ? "（必填）" : "（可修改）"}</Text>
            <TextInput
              value={displayName}
              onChangeText={setDisplayName}
              maxLength={40}
              placeholder={mode === "signup" ? "用于在设备列表里显示" : "已有账号可留空"}
              placeholderTextColor="#A8A49C"
              style={styles.input}
              accessibilityLabel="用户名"
            />

            <Text style={styles.fieldLabel}>密码</Text>
            <TextInput
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              placeholder="只用于登录，不保存在本机"
              placeholderTextColor="#A8A49C"
              style={styles.input}
              accessibilityLabel="密码"
            />

            {!!message && <Text style={styles.errorText}>{message}</Text>}

            <Pressable
              disabled={busy}
              onPress={() => submit(mode)}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, busy && styles.disabled]}
            >
              {busy ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.primaryButtonText}>
                  {mode === "signup" ? "注册并登录" : "登录"}
                </Text>
              )}
            </Pressable>

            <Text style={styles.hint}>
              Firebase 账号只同步备份元数据：进度、历史、事件名。照片、视频、电脑路径和凭据都不会上传云端。
            </Text>
          </View>

          <Pressable onPress={skip} style={({ pressed }) => [styles.skipButton, pressed && styles.pressed]}>
            <Text style={styles.skipText}>跳过登录，仅使用本机备份</Text>
          </Pressable>
          <Text style={styles.footer}>加入「影序」，随时回到素材与拍摄现场。</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safeArea: { flex: 1, backgroundColor: "#F4F2ED" },
  content: { paddingHorizontal: 20, paddingTop: 28, paddingBottom: 36, flexGrow: 1 },
  brandBlock: { marginBottom: 24 },
  eyebrow: { color: "#827D73", fontSize: 13, letterSpacing: 0.4 },
  brand: { color: "#1E2420", fontSize: 42, fontWeight: "700", letterSpacing: -1.2, marginTop: 6 },
  subtitle: { color: "#6F706A", fontSize: 14, lineHeight: 21, marginTop: 12 },
  noticeCard: { backgroundColor: "#E8F0EA", borderRadius: 16, padding: 16, marginBottom: 18 },
  noticeTitle: { color: "#273D31", fontSize: 13, fontWeight: "700" },
  noticeBody: { color: "#3C4A40", fontSize: 14, marginTop: 6 },
  card: { backgroundColor: "#FFFFFF", borderRadius: 18, padding: 18, borderWidth: 1, borderColor: "#E9E6E0" },
  modeRow: { flexDirection: "row", backgroundColor: "#F1EEE8", borderRadius: 12, padding: 4, marginBottom: 6 },
  modeTab: { flex: 1, minHeight: 40, alignItems: "center", justifyContent: "center", borderRadius: 9 },
  modeTabActive: { backgroundColor: "#FFFFFF" },
  modeText: { color: "#86837B", fontSize: 14, fontWeight: "600" },
  modeTextActive: { color: "#273D31", fontWeight: "700" },
  fieldLabel: { color: "#4A4B45", fontSize: 12, fontWeight: "600", marginBottom: 7, marginTop: 14 },
  input: {
    minHeight: 48,
    borderColor: "#E2DFD8",
    borderWidth: 1,
    borderRadius: 11,
    paddingHorizontal: 13,
    color: "#242823",
    fontSize: 15,
    backgroundColor: "#FEFDFC",
  },
  primaryButton: {
    minHeight: 50,
    backgroundColor: "#273D31",
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 18,
  },
  primaryButtonText: { color: "#FFFFFF", fontSize: 15, fontWeight: "700" },
  pressed: { opacity: 0.82 },
  disabled: { opacity: 0.6 },
  hint: { color: "#86837B", fontSize: 11, lineHeight: 17, marginTop: 14 },
  errorText: { color: "#A33A32", fontSize: 13, lineHeight: 19, marginTop: 12 },
  skipButton: { minHeight: 48, alignItems: "center", justifyContent: "center", marginTop: 18 },
  skipText: { color: "#27804A", fontSize: 14, fontWeight: "600" },
  footer: { color: "#9A968F", textAlign: "center", fontSize: 11, marginTop: 8 },
});
