// Dev-only entry, excluded from the production Vite HTML entry. Uses disposable fixtures only.
import { createApp } from "vue";
import App from "../src/App.vue";
import { createTauriApi } from "../src/api/tauri";
import "../src/style.css";
const token = new URL(location.href).searchParams.get("token");
if (!import.meta.env.DEV || !token)
  throw new Error("Explicit isolated fixture token required");
const app = createApp(App, {
  testTransport:
    "隔离测试：真实 Rust CLI；目录选择为固定临时样本，非原生 IPC。",
  createApi: (confirm) =>
    createTauriApi({
      confirm,
      invoke: async (command, args) => {
        const response = await fetch("http://127.0.0.1:5184/invoke", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Fixture-Token": token,
          },
          body: JSON.stringify({ command, args }),
        });
        if (!response.ok)
          throw new Error(`Fixture transport ${response.status}`);
        return response.json();
      },
    }),
});
app.config.errorHandler = (error) =>
  window.dispatchEvent(new CustomEvent("yingxu-error", { detail: error }));
app.mount("#app");
