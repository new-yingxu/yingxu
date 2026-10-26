import assert from 'node:assert/strict'
import { CloudError, createCloudClient, createModelConsent, modelFailure, modelRecoveryRequired } from '../evidence/r10-module/model-recovery-r10-entry.js'

let count = 0
const test = async (name, work) => { await work(); count++; console.log(`PASS ${name}`) }
const configured = { configured: true, provider: 'fixture', model: 'synthetic-model', region: 'local', requiresExplicitConsent: true, executionAuthorized: false }
const identity = { sessionId: 'r10-session', turnId: 'r10-turn' }
function fixture(code, status = 409) {
  const state = { calls: [], body: { detail: { code, automaticRetryAllowed: false } }, status }
  state.api = createCloudClient({ baseUrl: 'https://example.invalid', getUserToken: async () => 'synthetic-token', fetch: async (url, options) => {
    state.calls.push({ url, options })
    return new Response(JSON.stringify(state.body), { status: state.status })
  } })
  return state
}

await test('pending/uncertain 原样保留协议错误，提示不建议重发或新建轮次', async () => {
  for (const [code, status, expected] of [
    ['MODEL_REQUEST_PENDING', 409, /仍在服务端处理中/],
    ['MODEL_REQUEST_UNCERTAIN', 409, /结果未确认/],
    ['MODEL_REQUEST_COMPLETED_UNRETAINED', 409, /已经处理/],
  ]) {
    const f = fixture(code, status)
    let failure
    await assert.rejects(() => f.api.askModel('合成任务', identity, { explicitConsent: true }), error => {
      failure = error
      return error.code === code && error.status === status && error.automaticRetryAllowed === false
    })
    assert.equal(f.calls.length, 1)
    assert.match(modelFailure(failure).message, expected)
    assert.match(modelFailure(failure).message, /不会自动重发|不会重发|不要重发/)
    assert.doesNotMatch(modelFailure(failure).message, /主动新建轮次|建议.*重试/)
  }
})

await test('同轮 pending/uncertain 后锁住新轮次入口，不产生第二次模型请求', async () => {
  for (const code of ['MODEL_REQUEST_PENDING', 'MODEL_REQUEST_UNCERTAIN']) {
    let asks = 0
    const consent = createModelConsent({
      status: async () => configured,
      ask: async () => { asks++; throw new CloudError(code, 'fixture error', 409, false) },
    })
    const plan = await consent.prepare('合成任务', identity)
    await assert.rejects(() => consent.confirm(plan, true), { code })
    assert.equal(consent.requiresRecovery(), true)
    await assert.rejects(() => consent.prepare('合成任务', { ...identity, turnId: 'new-turn-must-not-send' }), { code: 'MODEL_RECOVERY_REQUIRED' })
    assert.equal(asks, 1)
    assert.match(modelFailure(new CloudError('MODEL_RECOVERY_REQUIRED', 'blocked', 0, false)).message, /阻止再次发送/)
  }
})

await test('resume 只使用原轮次和显式 resume:true，不含未定义字段', async () => {
  const f = fixture('ignored')
  f.body = { answer: '同轮恢复回答', matches: [], sessionId: identity.sessionId, turnId: identity.turnId, persisted: true, model: 'synthetic-model', provider: 'fixture', executionAuthorized: false }
  f.status = 200
  const reply = await f.api.resumeModel('合成任务', identity, { explicitConsent: true })
  assert.equal(reply.text, '同轮恢复回答')
  assert.deepEqual(JSON.parse(f.calls[0].options.body), { query: '合成任务', ...identity, useModel: true, resume: true })
  const denied = fixture('ignored')
  await assert.rejects(() => denied.api.resumeModel('合成任务', identity, { explicitConsent: false }), { code: 'MODEL_CONSENT_REQUIRED' })
  assert.equal(denied.calls.length, 0)
})

await test('普通模型首发不附带 resume，恢复字段不会自动启用', async () => {
  const f = fixture('ignored')
  f.body = { answer: '合成首发回答', matches: [], sessionId: identity.sessionId, turnId: identity.turnId, persisted: true, model: 'synthetic-model', provider: 'fixture', executionAuthorized: false }
  f.status = 200
  await f.api.askModel('合成任务', identity, { explicitConsent: true })
  assert.deepEqual(JSON.parse(f.calls[0].options.body), { query: '合成任务', ...identity, useModel: true })
})

await test('恢复需第二次明确确认且成功后才解除新轮次锁定', async () => {
  let asks = 0, resumes = 0, resumedQuery, resumedIdentity
  const consent = createModelConsent({
    status: async () => configured,
    ask: async () => { asks++; throw new CloudError('MODEL_REQUEST_PENDING', 'pending', 409, false) },
    resume: async (query, sameIdentity) => {
      resumes++; resumedQuery = query; resumedIdentity = sameIdentity
      return { text: '恢复成功', matches: [] }
    },
  })
  const original = await consent.prepare('合成任务', identity)
  await assert.rejects(() => consent.confirm(original, true), { code: 'MODEL_REQUEST_PENDING' })
  assert.equal(consent.canResume(), true)
  const originalRecoveryPlan = consent.getRecoveryPlan()
  await assert.rejects(() => consent.resume(false), { code: 'MODEL_CONSENT_REQUIRED' })
  assert.equal(resumes, 0)
  const reply = await consent.resume(true)
  assert.equal(reply.text, '恢复成功')
  assert.equal(resumedQuery, '合成任务')
  assert.deepEqual(resumedIdentity, identity)
  assert.equal(originalRecoveryPlan.turnId, identity.turnId)
  assert.equal(asks, 1)
  assert.equal(resumes, 1)
  assert.equal(consent.requiresRecovery(), false)
})

await test('服务端拒绝恢复时关闭恢复入口且不允许另起模型轮次', async () => {
  let asks = 0, resumes = 0
  const consent = createModelConsent({
    status: async () => configured,
    ask: async () => { asks++; throw new CloudError('MODEL_REQUEST_UNCERTAIN', 'uncertain', 409, false) },
    resume: async () => { resumes++; throw new CloudError('CHECKPOINT_NOT_FOUND', 'no checkpoint', 409, false) },
  })
  const plan = await consent.prepare('合成任务', identity)
  await assert.rejects(() => consent.confirm(plan, true), { code: 'MODEL_REQUEST_UNCERTAIN' })
  await assert.rejects(() => consent.resume(true), { code: 'CHECKPOINT_NOT_FOUND' })
  assert.equal(consent.canResume(), false)
  assert.equal(consent.requiresRecovery(), true)
  await assert.rejects(() => consent.prepare('不可改用新轮次', { ...identity, turnId: 'replacement-turn' }), { code: 'MODEL_RECOVERY_REQUIRED' })
  assert.equal(asks, 1)
  assert.equal(resumes, 1)
  assert.match(modelFailure(new CloudError('CHECKPOINT_NOT_FOUND', 'missing')).message, /未找到本轮恢复点/)
})

await test('请求开始前身份错误标记本轮未发送，可在完成身份连接后重新确认', async () => {
  let calls = 0
  const api = createCloudClient({ baseUrl: 'https://example.invalid', getUserToken: async () => null, fetch: async () => { calls++; throw Error('must not call') } })
  let failure
  await assert.rejects(() => api.askModel('合成任务', identity, { explicitConsent: true }), error => {
    failure = error
    return error.code === 'AUTH_REQUIRED' && error.modelDispatched === false
  })
  assert.equal(calls, 0)
  assert.match(modelFailure(failure).message, /身份未连接/)
  assert.equal(modelRecoveryRequired(failure), false)
})

await test('网络断开只调用一次并提示核对状态', async () => {
  let calls = 0
  const api = createCloudClient({ baseUrl: 'https://example.invalid', getUserToken: async () => 'synthetic-token', fetch: async () => { calls++; throw Error('synthetic network failure') } })
  let failure
  await assert.rejects(() => api.askModel('合成任务', identity, { explicitConsent: true }), error => {
    failure = error
    return error.code === 'CLOUD_UNREACHABLE' && error.automaticRetryAllowed === false
  })
  assert.equal(calls, 1)
  assert.equal(modelRecoveryRequired(failure), true)
  assert.match(modelFailure(failure).message, /核对本轮状态/)
  assert.doesNotMatch(modelFailure(failure).message, /主动新建轮次|请.*重试/)
})

console.log(`${count} R10 recovery checks passed; synthetic transport only, no real provider or backend changes.`)
