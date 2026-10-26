import assert from 'node:assert/strict'
import { CloudIdentityError, createCloudBaseIdentityClient } from '../evidence/r11-module/r11-entry.js'

let passed = 0
async function check(name, work) { await work(); passed++; console.log(`PASS ${name}`) }
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const valid = { accessToken: `header.${'x'.repeat(60)}.signature`, tokenType: 'Bearer', expiresIn: 900, ownerId: 'owner-r11', deviceId: 'device-r11', origin: 'desktop', deviceAttested: false }

await check('exchange only sends displayName and never caller-supplied owner/device/origin', async () => {
  let request
  const identity = createCloudBaseIdentityClient({ baseUrl: 'http://127.0.0.1:8787', displayName: 'R11 Mac', getCloudBaseAccountToken: async () => 'synthetic-account-token', now: () => 1000, fetch: async (url, options) => { request = { url, options }; return response(valid) } })
  const session = await identity.exchange()
  assert.equal(request.url, 'http://127.0.0.1:8787/v1/auth/session/exchange')
  assert.deepEqual(JSON.parse(request.options.body), { displayName: 'R11 Mac' })
  assert.equal(request.options.credentials, 'omit'); assert.equal(request.options.redirect, 'error'); assert.equal(request.options.cache, 'no-store')
  assert.equal(request.options.headers.Authorization, 'Bearer synthetic-account-token')
  assert.deepEqual(session, { ownerId: 'owner-r11', deviceId: 'device-r11', origin: 'desktop', deviceAttested: false, expiresAt: 901000 })
  assert.equal(JSON.stringify(session).includes(valid.accessToken), false)
  assert.equal(await identity.getUserToken(), valid.accessToken)
  await assert.rejects(() => identity.exchange(), { code: 'SESSION_ALREADY_ACTIVE' })
})

await check('short session expiry clears token and public session view', async () => {
  let now = 1000
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => 'synthetic-account-token', now: () => now, fetch: async () => response(valid) })
  await identity.exchange(); now += 900000
  assert.equal(await identity.getUserToken(), null); assert.equal(identity.session(), null)
})

await check('renew is explicit, uses fresh account auth plus old app session, and keeps the same device', async () => {
  let calls = 0, accountReads = 0, now = 2000
  const renewed = { ...valid, accessToken: `header.${'y'.repeat(60)}.signature`, ownerId: 'owner-r11', deviceId: 'device-r11' }
  const identity = createCloudBaseIdentityClient({ baseUrl: 'http://127.0.0.1:8787', displayName: 'R11', now: () => now,
    getCloudBaseAccountToken: async () => { accountReads++; return 'synthetic-fresh-account-token' },
    fetch: async (url, options) => {
      calls++
      if (url.endsWith('/exchange')) return response(valid)
      assert.equal(url, 'http://127.0.0.1:8787/v1/auth/session/renew')
      assert.deepEqual(JSON.parse(options.body), {})
      assert.equal(options.headers.Authorization, 'Bearer synthetic-fresh-account-token')
      assert.equal(options.headers['X-Yingxu-Session'], valid.accessToken)
      return response(renewed)
    },
  })
  await identity.exchange(); assert.equal(calls, 1); assert.equal(accountReads, 1)
  const before = identity.session(); now += 5000
  const after = await identity.renew()
  assert.equal(calls, 2); assert.equal(accountReads, 2)
  assert.equal(after.ownerId, before.ownerId); assert.equal(after.deviceId, before.deviceId); assert.equal(after.expiresAt, now + 900000)
  assert.equal(await identity.getUserToken(), renewed.accessToken)
})

await check('renew rejection by account owner clears the local session; transient 503 retains it', async () => {
  let mode = 'mismatch'
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => 'synthetic-account-token', fetch: async url => {
    if (url.endsWith('/exchange')) return response(valid)
    return mode === 'mismatch' ? response({ detail: { code: 'ACCOUNT_SESSION_MISMATCH' } }, 403) : response({ detail: { code: 'IDENTITY_PROVIDER_UNAVAILABLE' } }, 503)
  } })
  await identity.exchange(); await assert.rejects(() => identity.renew(), { code: 'ACCOUNT_SESSION_MISMATCH' }); assert.equal(identity.session(), null)
  await identity.exchange(); mode = 'unavailable'; await assert.rejects(() => identity.renew(), { code: 'IDENTITY_PROVIDER_UNAVAILABLE' }); assert.equal(await identity.getUserToken(), valid.accessToken)
})

await check('expired session never auto-renews and requires a fresh account login', async () => {
  let now = 1000, accountReads = 0, requests = 0
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', now: () => now, getCloudBaseAccountToken: async () => { accountReads++; return 'synthetic-account-token' }, fetch: async () => { requests++; return response(valid) } })
  await identity.exchange(); now += 900000
  await assert.rejects(() => identity.renew(), { code: 'SESSION_EXPIRED' })
  assert.equal(requests, 1); assert.equal(accountReads, 1); assert.equal(identity.session(), null)
})

await check('invalid CloudBase session is rejected without calling API', async () => {
  let calls = 0
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => null, fetch: async () => { calls++; throw Error('unexpected') } })
  await assert.rejects(() => identity.exchange(), { code: 'ACCOUNT_SESSION_REQUIRED' }); assert.equal(calls, 0)
})

await check('server error codes survive while token and response body remain private', async () => {
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => 'synthetic-account-token', fetch: async () => response({ detail: { code: 'INVALID_ACCOUNT_SESSION', token: 'do-not-leak' } }, 401) })
  await assert.rejects(() => identity.exchange(), error => error instanceof CloudIdentityError && error.code === 'INVALID_ACCOUNT_SESSION' && !error.message.includes('do-not-leak'))
  assert.equal(identity.session(), null)
})

await check('logout clears in-memory credentials before network call; backend revocation is confirmed', async () => {
  let identity, tokenAtLogout, request
  identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => 'synthetic-account-token', fetch: async (url, options) => {
    request = { url, options }
    if (url.endsWith('/logout')) tokenAtLogout = await identity.getUserToken()
    return url.endsWith('/exchange') ? response(valid) : response({ loggedOut: true, deviceRevoked: true })
  } })
  await identity.exchange(); await identity.logout()
  assert.equal(tokenAtLogout, null); assert.equal(await identity.getUserToken(), null); assert.equal(identity.session(), null)
  assert.equal(request.url, 'https://identity.example/v1/auth/logout'); assert.equal(request.options.headers.Authorization, `Bearer ${valid.accessToken}`)
})

await check('logout network failure still clears local memory and reports service confirmation missing', async () => {
  const identity = createCloudBaseIdentityClient({ baseUrl: 'https://identity.example', displayName: 'R11', getCloudBaseAccountToken: async () => 'synthetic-account-token', fetch: async url => url.endsWith('/exchange') ? response(valid) : Promise.reject(Error('sensitive transport detail')) })
  await identity.exchange(); await assert.rejects(() => identity.logout(), error => error.code === 'IDENTITY_UNREACHABLE' && !error.message.includes('sensitive'))
  assert.equal(await identity.getUserToken(), null)
})

await check('identity module does not persist credentials in browser storage', async () => {
  const { readFile } = await import('node:fs/promises')
  const source = await readFile(new URL('../src/api/cloud-identity.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/)
})

console.log(`${passed} R11 identity checks passed; synthetic HTTP only.`)
