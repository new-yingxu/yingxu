import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createModelConsent, executeModelSend } from '../evidence/model-consent-module/model-consent.js'

const configured = {
  configured: true,
  provider: 'ollama',
  model: 'qwen2.5:7b',
  region: 'local',
  requiresExplicitConsent: true,
  executionAuthorized: false,
}
const ledger = new Map()
const attempts = new Map()
let providerRequests = 0
const service = {
  status: async () => configured,
  ask: async (query, identity) => {
    providerRequests += 1
    const key = JSON.stringify([identity.sessionId, identity.turnId])
    attempts.set(key, (attempts.get(key) ?? 0) + 1)
    if (identity.sessionId === 'old-session') {
      const record = { state: 'awaiting_tools', query, modelRequests: 1, localToolRequests: 1 }
      ledger.set(key, record)
      throw Object.assign(new Error('synthetic uncertain continuation'), {
        code: 'LOCAL_AGENT_CONTINUATION_UNCONFIRMED',
        modelDispatched: true,
      })
    }
    ledger.set(key, { state: 'completed', query, modelRequests: 2, localToolRequests: 1 })
    return { text: '合成素材已找到。', matches: [] }
  },
}

const oldIdentity = { sessionId: 'old-session', turnId: 'old-turn' }
const oldConsent = createModelConsent(service)
const oldPlan = await oldConsent.prepare('只查合成索引 IMG_2048.jpg', oldIdentity)
await assert.rejects(() => oldConsent.confirm(oldPlan, true), {
  code: 'LOCAL_AGENT_CONTINUATION_UNCONFIRMED',
})
const oldKey = JSON.stringify([oldIdentity.sessionId, oldIdentity.turnId])
const oldLedgerBeforeNewConversation = structuredClone(ledger.get(oldKey))
assert.equal(oldConsent.requiresRecovery(), true)
assert.equal(providerRequests, 1)
assert.equal(attempts.get(oldKey), 1)
await assert.rejects(
  () => oldConsent.prepare('不可重发旧轮', oldIdentity),
  { code: 'MODEL_RECOVERY_REQUIRED' },
)
assert.equal(providerRequests, 1)
oldConsent.stop()

// The UI remounts ModelConsent only after the user accepts the separate new-chat warning.
const newIdentity = { sessionId: 'new-session', turnId: 'new-turn' }
const newConsent = createModelConsent(service)
const reply = await executeModelSend(newConsent, '只查合成索引 IMG_2048.jpg', newIdentity)
assert.equal(reply.text, '合成素材已找到。')
assert.notEqual(newIdentity.sessionId, oldIdentity.sessionId)
assert.notEqual(newIdentity.turnId, oldIdentity.turnId)
assert.equal(providerRequests, 2)
assert.equal(attempts.get(oldKey), 1)
assert.deepEqual(ledger.get(oldKey), oldLedgerBeforeNewConversation)
assert.deepEqual(ledger.get(JSON.stringify([newIdentity.sessionId, newIdentity.turnId])), {
  state: 'completed',
  query: '只查合成索引 IMG_2048.jpg',
  modelRequests: 2,
  localToolRequests: 1,
})

const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
const consentComponent = await readFile(new URL('../src/components/ModelConsent.vue', import.meta.url), 'utf8')
assert.match(app, /:key="agentConversationKey"/)
assert.match(app, /@click="requestNewAgentConversation"/)
assert.match(app, /@click="confirmNewAgentConversation"/)
assert.match(app, /agentHasArchivedUnresolvedTurn/)
assert.match(app, /agentConversationKey\.value \+= 1/)
assert.match(consentComponent, /const sessionId=crypto\.randomUUID\(\)/)
assert.match(consentComponent, /turnId:crypto\.randomUUID\(\)/)
assert.match(consentComponent, /requiresRecovery:\s*\(\)\s*=>/)
assert.match(consentComponent, /isBusy:\s*\(\)\s*=>/)
assert.match(consentComponent, /if\(props\.autoConfirm\)/)
assert.match(app, /:auto-confirm="true"/)
const tray = await readFile(new URL('../src/components/TrayAgentPanel.vue', import.meta.url), 'utf8')
assert.match(tray, /confirmModelUse: async \(\) => true/)
assert.match(tray, /:auto-confirm="true"/)

console.log('PASS unresolved old turn is not replayed')
console.log('PASS Send dispatches one new turn with a separate session and turn')
console.log('PASS old ledger record remains unchanged')
console.log(`PASS provider request count is bounded to ${providerRequests} synthetic local calls`)
