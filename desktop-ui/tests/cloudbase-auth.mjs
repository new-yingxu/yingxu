import assert from 'node:assert/strict'
import { CloudIdentityError, createCloudBaseIdentityClient } from '../evidence/cloud-identity-module/cloud-identity.js'
import { cloudBaseReadiness, createCloudBaseAuthService } from '../evidence/cloudbase-auth-module/cloudbase-auth.js'

let passed = 0
async function check(name, work) { await work(); passed++; console.log(`PASS ${name}`) }
const readyConfig = {
  envId: 'synthetic-env', region: 'ap-shanghai', clientId: 'public-client-id',
  allowedOrigins: 'tauri://localhost,http://127.0.0.1:5173', authReady: 'true',
  identityApiUrl: 'https://identity.example', identityApiReady: 'true',
}
const identityReply = { accessToken: `header.${'z'.repeat(60)}.signature`, tokenType: 'Bearer', expiresIn: 900, ownerId: 'owner-synthetic', deviceId: 'device-synthetic', origin: 'desktop', deviceAttested: false }
function fakeAuth(overrides = {}) {
  const calls = []
  const verified = async (name, result = {}) => async (input) => { calls.push([name, input]); return { data: result, error: null } }
  const auth = {
    async signInWithOtp(input) { calls.push(['signInWithOtp', input]); return { data: { verifyOtp: await verified('verifyOtp', { user: { uid: 'synthetic-user' } }) }, error: null } },
    async signInWithPassword(input) { calls.push(['signInWithPassword', input]); return { data: { user: { uid: 'synthetic-user' } }, error: null } },
    async signUp(input) { calls.push(['signUp', input]); return { data: { verifyOtp: await verified('verifyOtp', { user: { uid: 'synthetic-user' } }) }, error: null } },
    async resetPasswordForEmail(input) { calls.push(['resetPasswordForEmail', input]); return { data: { updateUser: await verified('updateUser') }, error: null } },
    async getSession() { calls.push(['getSession']); return { data: { session: { access_token: 'synthetic-session' } }, error: null } },
    async getCurrentUser() { calls.push(['getCurrentUser']); return { data: { user: { uid: 'synthetic-user', email: 'known@example.invalid', nickname: 'Known User' } }, error: null } },
    async getAccessToken() { calls.push(['getAccessToken']); return { accessToken: 'synthetic-cloudbase-account-token', env: 'synthetic-env' } },
    async signOut() { calls.push(['signOut']); return {} },
    ...overrides,
  }
  return { auth, calls }
}
function identityClient(fetcher, auth, now = Date.now) {
  return createCloudBaseIdentityClient({
    baseUrl: 'https://identity.example', displayName: '影序桌面端',
    getCloudBaseAccountToken: async () => (await auth.getAccessToken()).accessToken,
    fetch: fetcher,
    now,
  })
}
function reply(body, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }) }

await check('readiness fails closed for missing origin, console gate, and non-HTTPS identity URL', async () => {
  assert.equal(cloudBaseReadiness(readyConfig, 'https://unexpected.example').ready, false)
  assert.equal(cloudBaseReadiness({ ...readyConfig, authReady: 'false' }, 'tauri://localhost').emailOtp, false)
  assert.equal(cloudBaseReadiness({ ...readyConfig, identityApiUrl: 'http://identity.example' }, 'tauri://localhost').ready, false)
  assert.equal(cloudBaseReadiness(readyConfig, 'tauri://localhost').ready, true)
})

await check('email OTP requires a matching verified existing-account challenge, then exchanges only the access token', async () => {
  const { auth, calls } = fakeAuth()
  const requestLog = []
  const identity = identityClient(async (url, options) => { requestLog.push({ url, options }); return reply(identityReply) }, auth, () => 1000)
  const service = createCloudBaseAuthService({ auth, identity, readiness: cloudBaseReadiness(readyConfig, 'tauri://localhost'), now: () => 1000 })
  await assert.rejects(() => service.login('email-code', 'known@example.invalid', '123456'), { code: 'VERIFICATION_REQUIRED' })
  await service.sendEmailCode(' Known@Example.Invalid ', 'login')
  const session = await service.login('email-code', 'known@example.invalid', '123456')
  assert.deepEqual(calls.filter(([name]) => ['signInWithOtp', 'verifyOtp'].includes(name)).map(([name]) => name), ['signInWithOtp', 'verifyOtp'])
  assert.deepEqual(calls.find(([name]) => name === 'signInWithOtp')[1], { email: 'known@example.invalid', options: { shouldCreateUser: false } })
  assert.deepEqual(calls.find(([name]) => name === 'verifyOtp')[1], { token: '123456' })
  assert.equal(requestLog[0].url, 'https://identity.example/v1/auth/session/exchange')
  assert.deepEqual(JSON.parse(requestLog[0].options.body), { displayName: '影序桌面端' })
  assert.equal(requestLog[0].options.headers.Authorization, 'Bearer synthetic-cloudbase-account-token')
  assert.equal(JSON.stringify(requestLog[0].options).includes('123456'), false)
  assert.deepEqual(session, { userId: 'owner-synthetic', displayName: 'Known User', email: 'known@example.invalid', deviceId: 'device-synthetic', expiresAt: new Date(901000).toISOString() })
  assert.equal(await identity.getUserToken(), identityReply.accessToken)
})

await check('email signup verifies code before password registration, stores nickname, and never sends credentials to identity API', async () => {
  const { auth, calls } = fakeAuth()
  const requests = []
  const identity = identityClient(async (url, options) => { requests.push({ url, options }); return reply(identityReply) }, auth)
  const service = createCloudBaseAuthService({ auth, identity, readiness: cloudBaseReadiness(readyConfig, 'tauri://localhost') })
  await service.sendEmailCode('new@example.invalid', 'register', { nickname: '新用户', password: 'synthetic-password' })
  const session = await service.registerEmail({ email: 'new@example.invalid', verificationCode: '654321', nickname: '新用户', password: 'synthetic-password' })
  assert.deepEqual(calls.filter(([name]) => ['signUp', 'verifyOtp'].includes(name)).map(([name]) => name), ['signUp', 'verifyOtp'])
  assert.deepEqual(calls.find(([name]) => name === 'signUp')[1], { email: 'new@example.invalid', nickname: '新用户', password: 'synthetic-password' })
  assert.deepEqual(calls.find(([name]) => name === 'verifyOtp')[1], { token: '654321' })
  assert.deepEqual(JSON.parse(requests[0].options.body), { displayName: '影序桌面端' })
  assert.equal(JSON.stringify(requests[0]).includes('synthetic-password'), false)
  assert.equal(session.userId, 'owner-synthetic')
})

await check('CloudBase provider failures are sanitized before reaching the login UI', async () => {
  const { auth } = fakeAuth({ async signInWithOtp() { throw new Error('sensitive-provider-response synthetic-password') } })
  const identity = identityClient(async () => { throw new Error('unexpected identity request') }, auth)
  const service = createCloudBaseAuthService({ auth, identity, readiness: cloudBaseReadiness(readyConfig, 'tauri://localhost') })
  await assert.rejects(() => service.sendEmailCode('known@example.invalid', 'login'), error => error.code === 'EMAIL_CODE_SEND_FAILED' && !error.message.includes('sensitive-provider-response') && !error.message.includes('synthetic-password'))
})

await check('email/password login uses CloudBase and restore exchanges an existing CloudBase login', async () => {
  const { auth, calls } = fakeAuth()
  const requests = []
  const identity = identityClient(async (url, options) => { requests.push({ url, options }); return url.endsWith('/logout') ? reply({ loggedOut: true, deviceRevoked: true }) : reply(identityReply) }, auth)
  const service = createCloudBaseAuthService({ auth, identity, readiness: cloudBaseReadiness(readyConfig, 'tauri://localhost') })
  await service.login('email-password', 'known@example.invalid', 'synthetic-password')
  assert.deepEqual(calls.find(([name]) => name === 'signInWithPassword')[1], { email: 'known@example.invalid', password: 'synthetic-password' })
  await service.logout()
  const restored = await service.restore()
  assert.equal(restored.userId, 'owner-synthetic')
  assert.equal(requests.some(({ url }) => url.endsWith('/exchange')), true)
})

await check('unready configuration blocks SDK and HTTP calls; logout clears both sessions', async () => {
  let sdkCalls = 0, httpCalls = 0
  const auth = fakeAuth().auth
  for (const method of ['signInWithOtp', 'signInWithPassword', 'signUp', 'resetPasswordForEmail', 'getSession', 'getCurrentUser', 'getAccessToken', 'signOut']) {
    const original = auth[method]
    auth[method] = async (...args) => { sdkCalls++; return original(...args) }
  }
  const identity = identityClient(async () => { httpCalls++; return reply(identityReply) }, auth)
  const service = createCloudBaseAuthService({ auth, identity, readiness: cloudBaseReadiness({ ...readyConfig, authReady: 'false' }, 'tauri://localhost') })
  await assert.rejects(() => service.sendEmailCode('known@example.invalid', 'login'), { code: 'AUTH_NOT_CONFIGURED' })
  await assert.rejects(() => service.login('email-password', 'known-user', 'password'), { code: 'AUTH_NOT_CONFIGURED' })
  assert.equal(await service.restore(), null)
  assert.equal(sdkCalls, 0); assert.equal(httpCalls, 0)

  const signed = await createCloudBaseAuthService({ auth: fakeAuth().auth, identity: { ...identity, async logout() { throw new CloudIdentityError('LOGOUT_TEST', 'synthetic') } }, readiness: cloudBaseReadiness(readyConfig, 'tauri://localhost') })
  await assert.rejects(() => signed.logout(), { code: 'LOGOUT_TEST' })
})

console.log(`${passed} CloudBase Authentication v3 checks passed; all credentials, tokens, and HTTP replies were synthetic.`)
