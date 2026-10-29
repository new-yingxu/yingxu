import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { agentModelDisplayLabel, agentModelSelection, canSendAgent, friendlyUiError, presetDisplayName } from "../evidence/ui-safety-module/ui-safety.js";

const cloudConfig = { configured: true, provider: "deepseek", model: "deepseek-chat", secretStorage: "os_keychain" };
assert.equal(agentModelSelection(cloudConfig, true), "", "local mode must not expose a cloud selection value");
assert.equal(agentModelDisplayLabel(cloudConfig, true), "本机模式 · 请选择 Ollama");
assert.equal(canSendAgent(true, cloudConfig), false, "local mode must not dispatch while the retained provider is cloud");
assert.equal(agentModelSelection(cloudConfig, false), "cloud:deepseek:deepseek-chat", "cloud mode keeps its configured model");
assert.equal(canSendAgent(false, cloudConfig), true);
assert.match(await readFile(new URL("../src/App.vue", import.meta.url), "utf8"), /if \(localWorkspace\.value && !agentCanSend\.value\)[\s\S]*?return;[\s\S]*?modelConsentRef\.value\.submit\(text\)/);
console.log("PASS local Agent selection and send guard fail closed for cloud config; cloud mode remains available");

assert.equal(friendlyUiError(Object.assign(new Error("已取消目录选择"), { code: "USER_CANCELLED" })), "已取消目录选择，尚未开始扫描。");
assert.equal(friendlyUiError(Object.assign(new Error("cancelled"), { code: "USER_CANCELLED" })), "已取消操作，未执行更改。");
assert.equal(friendlyUiError(Object.assign(new Error("not ready"), { code: "REPORT_NOT_READY" })), "当前任务尚未生成报告，请在任务完成后再查看。");
assert.equal(friendlyUiError(Object.assign(new Error("unexpected"), { code: "INTERNAL_CODE" })), null);
console.log("PASS cancellation and unready-report feedback hide internal codes");

const presets = [
  { id: "preset-default", name: "新建预设", pattern: "日期", isDefault: false },
  { id: "preset-second", name: "新建预设", pattern: "项目", isDefault: false },
  { id: "customer-01", name: "客户交付", pattern: "客户/项目", isDefault: false },
];
assert.notEqual(presetDisplayName(presets[0], 0, presets), presetDisplayName(presets[1], 1, presets));
assert.match(presetDisplayName(presets[0], 0, presets), /#1$/);
assert.equal(presetDisplayName(presets[2], 2, presets), "客户交付");
console.log("PASS preset labels distinguish duplicate/default names and preserve unique names");

const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
assert.match(app, /:checked="isMock \? settings\.startOnLogin : false"[\s\S]*?:disabled="!isMock"/);
assert.match(app, /本版本尚不支持开机启动/);
assert.match(app, /:checked="isMock \? settings\.backgroundOnClose : false"[\s\S]*?:disabled="!isMock"/);
console.log("PASS unsupported launch/background controls render unchecked and disabled in native mode");

assert.match(app, /const libraryQuery = ref\(""\);\s*const historyQuery = ref\(""\);/);
assert.match(app, /api\.library\.searchPage\(\s*libraryQuery\.value/);
assert.match(app, /api\.history\.list\(historyQuery\.value/);
assert.match(app, /v-model="libraryQuery"/);
assert.match(app, /v-model="historyQuery"/);
console.log("PASS library and archive search inputs and requests use isolated query state");
assert.match(app, /<CloudModelSettings v-if="!localWorkspace"/);
assert.match(app, /现有云端 Key 保留在系统钥匙串中，不会被选择或调用/);
