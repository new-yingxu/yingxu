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
  cloudConfigReady,
  createCloudClient,
  markLoginSkipped,
} from "../services/cloudbase-auth";

type Mode = "signin" | "signup";

export default function LoginScreen() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [existingUser, setExistingUser] = useState("");
  const configReady = cloudConfigReady();

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

  function resetFlow(nextMode: Mode) {
    setMode(nextMode);
    setMessage("");
    setVerificationCode("");
    setCodeSent(false);
  }

  async function sendCode() {
    setMessage("");
    const address = email.trim();
    if (!address) {
      setMessage("请先填写邮箱地址。");
      return;
    }
    if (mode === "signup") {
      if (!displayName.trim()) {
        setMessage("注册前请先填写用户名。");
        return;
      }
      if (password.length < 8) {
        setMessage("注册密码至少 8 个字符。");
        return;
      }
    }
    setBusy(true);
    try {
      const client = await createCloudClient();
      if (!client.configured) throw new Error("云端登录尚未配置，请先使用本机模式。");
      await client.sendEmailCode(address, "register");
      setCodeSent(true);
      setMessage("验证码已发送，请查收邮箱。");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "验证码发送失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setMessage("");
    const address = email.trim();
    const name = displayName.trim();
    if (!address || !password) {
      setMessage("请填写邮箱和密码。");
      return;
    }
    if (mode === "signup") {
      if (!name) {
        setMessage("注册时请填写用户名。");
        return;
      }
      if (!verificationCode.trim()) {
        setMessage("请填写邮箱验证码。");
        return;
      }
    }
    setBusy(true);
    try {
      const client = await createCloudClient();
      if (!client.configured) throw new Error("云端登录尚未配置，请先使用本机模式。");
      await client.signIn(address, password, mode === "signup", name, verificationCode);
      await clearLoginSkipped();
      setPassword("");
      setVerificationCode("");
      setCodeSent(false);
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
              使用影序账号登录，与桌面端共享同一账号，多端同步备份进度与历史元数据。不登录也能使用手机本机备份。
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

          {!configReady ? (
            <View style={styles.warnCard}>
              <Text style={styles.warnText}>
                云端登录尚未配置（缺少 EXPO_PUBLIC_CLOUDBASE_* 环境变量）。可先跳过登录使用本机备份。
              </Text>
            </View>
          ) : null}

          <View style={styles.card}>
            <View style={styles.modeRow}>
              <Pressable
                onPress={() => {
                  resetFlow("signin");
                }}
                style={[styles.modeTab, mode === "signin" && styles.modeTabActive]}
              >
                <Text style={[styles.modeText, mode === "signin" && styles.modeTextActive]}>登录</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  resetFlow("signup");
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

            {mode === "signup" ? (
              <>
                <Text style={styles.fieldLabel}>用户名（必填）</Text>
                <TextInput
                  value={displayName}
                  onChangeText={setDisplayName}
                  maxLength={40}
                  placeholder="用于在设备列表里显示"
                  placeholderTextColor="#A8A49C"
                  style={styles.input}
                  accessibilityLabel="用户名"
                />
              </>
            ) : null}

            <Text style={styles.fieldLabel}>密码{mode === "signup" ? "（至少 8 位）" : ""}</Text>
            <TextInput
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              placeholder="只用于登录，不保存在本机"
              placeholderTextColor="#A8A49C"
              style={styles.input}
              accessibilityLabel="密码"
            />

            {mode === "signup" ? (
              <>
                <Text style={styles.fieldLabel}>邮箱验证码</Text>
                <View style={styles.codeRow}>
                  <TextInput
                    autoCapitalize="characters"
                    keyboardType="number-pad"
                    value={verificationCode}
                    onChangeText={setVerificationCode}
                    maxLength={8}
                    placeholder="6 位验证码"
                    placeholderTextColor="#A8A49C"
                    style={[styles.input, styles.codeInput]}
                    accessibilityLabel="邮箱验证码"
                  />
                  <Pressable
                    disabled={busy}
                    onPress={sendCode}
                    style={({ pressed }) => [styles.codeButton, pressed && styles.pressed, busy && styles.disabled]}
                  >
                    <Text style={styles.codeButtonText}>{codeSent ? "重新发送" : "获取验证码"}</Text>
                  </Pressable>
                </View>
              </>
            ) : null}

            {!!message && <Text style={styles.messageText}>{message}</Text>}

            <Pressable
              disabled={busy || (mode === "signup" && !codeSent)}
              onPress={() => submit()}
              style={({ pressed }) => [
                styles.primaryButton,
                pressed && styles.pressed,
                (busy || (mode === "signup" && !codeSent)) && styles.disabled,
              ]}
            >
              {busy ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.primaryButtonText}>
                  {mode === "signup" ? "验证并创建账号" : "登录"}
                </Text>
              )}
            </Pressable>

            <Text style={styles.hint}>
              影序账号（CloudBase）只同步任务摘要：进度、状态与设备。照片、视频、原片路径和笔记不会上传云端。
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
  warnCard: { backgroundColor: "#F6EFE3", borderRadius: 16, padding: 16, marginBottom: 18 },
  warnText: { color: "#6B5A32", fontSize: 13, lineHeight: 19 },
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
  codeRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  codeInput: { flex: 1 },
  codeButton: {
    minHeight: 48,
    paddingHorizontal: 14,
    borderRadius: 11,
    borderWidth: 1,
    borderColor: "#273D31",
    alignItems: "center",
    justifyContent: "center",
  },
  codeButtonText: { color: "#273D31", fontSize: 13, fontWeight: "700" },
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
  messageText: { color: "#6B5A32", fontSize: 13, lineHeight: 19, marginTop: 12 },
  skipButton: { minHeight: 48, alignItems: "center", justifyContent: "center", marginTop: 18 },
  skipText: { color: "#27804A", fontSize: 14, fontWeight: "600" },
  footer: { color: "#9A968F", textAlign: "center", fontSize: 11, marginTop: 8 },
});
