import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const moduleDir = process.env.YINGXU_UI_TEST_MODULE_DIR
const modulePath = moduleDir ? pathToFileURL(resolve(moduleDir, 'ollama-onboarding.js')).href : '../evidence/ollama-onboarding-module/ollama-onboarding.js'
const { ollamaSetupView, REQUIRED_LOCAL_MODEL, REQUIRED_MODEL_PULL_COMMAND } = await import(modulePath)
const base = { configured: false, requiresExplicitConsent: true, executionAuthorized: false, cloudFallback: false }

const cases = [
  [{ ...base, configured: true, provider: 'ollama', model: 'qwen2.5:7b', region: 'local', availability: 'ready' }, 'ready', '本机模型已就绪'],
  [{ ...base, availability: 'ollama_unavailable' }, 'ollama_unavailable', '尚未检测到 Ollama'],
  [{ ...base, availability: 'model_not_installed' }, 'model_not_installed', 'Ollama 已运行，所需模型尚未安装'],
  [{ ...base, availability: 'sidecar_unavailable' }, 'sidecar_unavailable', '影序本机 Agent 服务不可用'],
]
for (const [status, state, title] of cases) {
  const view = ollamaSetupView(status)
  assert.equal(view.state, state)
  assert.equal(view.title, title)
  assert.match(view.detail, /本机|不会|仍需/)
}
assert.equal(ollamaSetupView(null, false).state, 'unknown')
assert.equal(REQUIRED_LOCAL_MODEL, 'qwen2.5:7b')
assert.equal(REQUIRED_MODEL_PULL_COMMAND, 'ollama pull qwen2.5:7b')
console.log(`${cases.length + 1} Ollama onboarding state checks passed.`)
