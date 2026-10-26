// R11 local-only acceptance. Requires the isolated cloud worktree and existing Ollama; never calls CloudBase.
import assert from 'node:assert/strict'
import { createHash, generateKeyPairSync, createPublicKey, verify } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, writeFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import { createCloudBaseIdentityClient, createCloudClient } from '../evidence/r11-module/r11-entry.js'

const cloudRoot = process.argv[2], python = process.argv[3]
if (!cloudRoot || !python) throw Error('Usage: node tests/ollama-e2e-r11.mjs <cloud-worktree-root> <python-path>')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const evidence = path.join(root, 'evidence')
await mkdir(evidence, { recursive: true })
const run = await mkdtemp(path.join(evidence, 'r11-local-identity-ollama-'))
const providerCallsFile = path.join(run, 'provider-count.txt')
const toolCallsFile = path.join(run, 'tool-calls.json')
const identityCallsFile = path.join(run, 'identity-http.json')
const turnCallsFile = path.join(run, 'turn-count.txt')
const logoutCallsFile = path.join(run, 'logout-count.txt')
const renewalCallsFile = path.join(run, 'renewal-count.txt')
await writeFile(providerCallsFile, '0'); await writeFile(toolCallsFile, '[]'); await writeFile(identityCallsFile, '[]'); await writeFile(turnCallsFile, '0'); await writeFile(logoutCallsFile, '0'); await writeFile(renewalCallsFile, '[]')
const accountTokenA = 'r11-synthetic-account-token-owner-a'
const accountTokenB = 'r11-synthetic-account-token-owner-b'
const accountTokenBMobile = 'r11-synthetic-account-token-owner-b-mobile'
const invalidAccountToken = 'r11-synthetic-invalid-account-token'
const model = 'qwen2.5:7b'
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048, privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } })
const modelConfiguration = JSON.stringify({ 'owner-r11-a': { provider: 'ollama', model } })
const filesToHash = [
  'cloud-service/app/main.py', 'cloud-service/app/config.py', 'cloud-service/app/auth.py',
  'cloud-service/app/cloudbase_identity.py', 'cloud-service/app/session_tokens.py',
  'cloud-service/app/db.py', 'cloud-service/app/models.py', 'cloud-service/app/store.py',
  'cloud-service/app/graph_checkpoints.py', 'cloud-service/app/agent_graph.py',
  'cloud-service/app/agent_tools.py', 'cloud-service/app/model_provider.py',
]
const digest = async file => createHash('sha256').update(await readFile(path.join(cloudRoot, file))).digest('hex')
const sourceHashesBefore = Object.fromEntries(await Promise.all(filesToHash.map(async file => [file, await digest(file)])))
const choosePort = async () => {
  const server = createServer(); await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', error => error ? reject(error) : resolve()))
  const port = server.address().port; await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())); return port
}
const vitePort = await choosePort()
const env = {
  PATH: '/tmp/yingxu-cloud-py311/bin:/usr/bin:/bin', HOME: run, LANG: 'en_US.UTF-8',
  APP_ENV: 'development', AUTH_MODE: 'cloudbase',
  DATABASE_URL: `sqlite:///${path.join(run, 'cloud.sqlite')}`,
  APP_VERSION: '0.4.0-r11-isolated-identity-ollama',
  CLOUDBASE_ENV_ID: 'r11-fixture', CLOUDBASE_APIKEY: '',
  CLOUDBASE_CLIENT_ORIGINS: JSON.stringify({ 'r11-desktop-client': 'desktop', 'r11-mobile-client': 'mobile' }),
  SESSION_PRIVATE_KEY: privateKey, SESSION_KEY_ID: 'r11-ephemeral',
  JWT_ISSUER: 'https://r11.invalid', JWT_AUDIENCE: 'yingxu-r11-local', SESSION_VERIFICATION_JWKS: '',
  CORS_ORIGINS: `http://127.0.0.1:${vitePort}`,
  MODEL_OWNER_CREDENTIALS: modelConfiguration,
  HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '', http_proxy: '', https_proxy: '', all_proxy: '', NO_PROXY: '*', no_proxy: '*',
  PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
  R11_PROVIDER_CALLS: providerCallsFile, R11_TOOL_CALLS: toolCallsFile, R11_IDENTITY_CALLS: identityCallsFile, R11_TURN_CALLS: turnCallsFile,
  R11_RENEW_CALLS: renewalCallsFile,
  R11_LOGOUT_CALLS: logoutCallsFile,
  R11_ACCOUNT_A: accountTokenA, R11_ACCOUNT_B: accountTokenB,
  R11_ACCOUNT_B_MOBILE: accountTokenBMobile,
}
const serverCode = String.raw`
import json, os
from pathlib import Path
from threading import local
from urllib.error import HTTPError
from urllib.parse import urlparse
import uvicorn
from app import main
from app import cloudbase_identity as identity
from app import model_provider
from app.agent_tools import ReadOnlyToolRegistry

accounts = {os.environ['R11_ACCOUNT_A']: ('owner-r11-a','r11-desktop-client'), os.environ['R11_ACCOUNT_B']: ('owner-r11-b','r11-desktop-client'), os.environ['R11_ACCOUNT_B_MOBILE']: ('owner-r11-b','r11-mobile-client')}
identity_marker = Path(os.environ['R11_IDENTITY_CALLS'])
provider_marker = Path(os.environ['R11_PROVIDER_CALLS'])
tool_marker = Path(os.environ['R11_TOOL_CALLS'])
turn_marker = Path(os.environ['R11_TURN_CALLS'])
renew_marker = Path(os.environ['R11_RENEW_CALLS'])
logout_marker = Path(os.environ['R11_LOGOUT_CALLS'])
class FakeResponse:
    def __init__(self, body): self.body = json.dumps(body).encode()
    def read(self, limit=-1): return self.body if limit < 0 else self.body[:limit]
    def __enter__(self): return self
    def __exit__(self, *args): return False
class FakeOpener:
    def open(self, request, timeout=5):
        url = urlparse(request.full_url)
        headers = {key.lower(): value for key, value in request.header_items()}
        token = headers.get('authorization', '').removeprefix('Bearer ')
        rows = json.loads(identity_marker.read_text())
        rows.append({'path': url.path, 'hasBearer': bool(token), 'timeout': timeout})
        identity_marker.write_text(json.dumps(rows))
        account = accounts.get(token)
        if account is None:
            raise HTTPError(request.full_url, 401, 'invalid synthetic account session', {}, None)
        owner, client_id = account
        if url.path.endswith('/user/me'):
            return FakeResponse({'sub': owner, 'status': 'ACTIVE', 'type': 'external', 'internal_user_type': 'generalUser', 'providers': [{'id':'wechat'}]})
        if url.path.endswith('/token/introspect'):
            return FakeResponse({'sub': owner, 'token_type':'Bearer', 'client_id':client_id, 'active':True})
        raise HTTPError(request.full_url, 404, 'unexpected identity endpoint', {}, None)
identity.build_opener = lambda *args, **kwargs: FakeOpener()

original_provider_request = model_provider._tool_provider_request
def counted_provider_request(*args, **kwargs):
    provider_marker.write_text(str(int(provider_marker.read_text()) + 1))
    return original_provider_request(*args, **kwargs)
model_provider._tool_provider_request = counted_provider_request
model_context = local()
original_generate = main.generate_answer
def counted_generate(*args, **kwargs):
    model_context.active = True
    try: return original_generate(*args, **kwargs)
    finally: model_context.active = False
main.generate_answer = counted_generate
original_tool_invoke = ReadOnlyToolRegistry.invoke
def counted_tool_invoke(self, name, arguments):
    result = original_tool_invoke(self, name, arguments)
    rows = json.loads(tool_marker.read_text())
    result_rows = result.get('matches', result.get('jobs', [])) if isinstance(result, dict) else []
    rows.append({'tool': name, 'source': 'ollama_model_tool' if getattr(model_context, 'active', False) else 'server_initial_retrieval', 'arguments': arguments,
        'returnedJobs': [{'jobId': row.get('jobId'), 'name': row.get('name'), 'state': row.get('state')} for row in result_rows if isinstance(row, dict)]})
    tool_marker.write_text(json.dumps(rows,ensure_ascii=False))
    return result
ReadOnlyToolRegistry.invoke = counted_tool_invoke

class CountTurns:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and scope.get('method') == 'POST' and scope.get('path') == '/v1/agent/turns':
            turn_marker.write_text(str(int(turn_marker.read_text()) + 1))
        if scope.get('type') == 'http' and scope.get('method') == 'POST' and scope.get('path') == '/v1/auth/session/renew':
            rows = json.loads(renew_marker.read_text())
            names = [key.decode('latin1').lower() for key, value in scope.get('headers', [])]
            rows.append({'hasAccountBearer': b'authorization' in [key.lower() for key, value in scope.get('headers', [])], 'hasPreviousSessionHeader': b'x-yingxu-session' in [key.lower() for key, value in scope.get('headers', [])], 'bodyIsEmptyObject': None})
            renew_marker.write_text(json.dumps(rows))
        if scope.get('type') == 'http' and scope.get('method') == 'POST' and scope.get('path') == '/v1/auth/logout':
            logout_marker.write_text(str(int(logout_marker.read_text()) + 1))
        await self.app(scope, receive, send)
uvicorn.run(CountTurns(main.app), host='127.0.0.1', port=0, log_level='info', access_log=False)
`
const api = spawn(python, ['-c', serverCode], { cwd: path.join(cloudRoot, 'cloud-service'), env, stdio: ['ignore', 'pipe', 'pipe'] })
let vite, baseUrl, apiStopped = false, viteStopped = false
const logs = { api: '', vite: '' }
let resolveApi, rejectApi
const apiStarted = new Promise((resolve, reject) => { resolveApi = resolve; rejectApi = reject })
const apiTimer = setTimeout(() => rejectApi(Error('Isolated CloudBase-session API did not start')), 20000)
for (const stream of [api.stdout, api.stderr]) stream.on('data', chunk => {
  logs.api += chunk.toString()
  const match = logs.api.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/)
  if (match) { baseUrl = match[1]; clearTimeout(apiTimer); resolveApi(baseUrl) }
})
api.on('error', rejectApi)
api.on('exit', code => { if (!baseUrl) rejectApi(Error(`Isolated API exited before startup (${code})`)) })

const recordRequests = []
const nativeFetch = globalThis.fetch
globalThis.fetch = async (url, options) => {
  const response = await nativeFetch(url, options)
  recordRequests.push({ method: options?.method ?? 'GET', path: new URL(url).pathname, status: response.status })
  return response
}
const waitApi = async () => {
  const end = Date.now() + 10000
  while (Date.now() < end) { try { if ((await nativeFetch(baseUrl + '/healthz')).ok) return; } catch {} await new Promise(resolve => setTimeout(resolve, 100)) }
  throw Error('Local CloudBase-auth API /healthz did not become healthy')
}
const verifyJwt = async (token, jwks) => {
  const parts = token.split('.')
  assert.equal(parts.length, 3)
  const header = JSON.parse(Buffer.from(parts[0], 'base64url').toString())
  const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString())
  assert.equal(header.alg, 'RS256'); assert.equal(header.kid, 'r11-ephemeral')
  const key = createPublicKey({ key: jwks.keys[0], format: 'jwk' })
  assert.equal(verify('RSA-SHA256', Buffer.from(`${parts[0]}.${parts[1]}`), key, Buffer.from(parts[2], 'base64url')), true)
  assert.equal(claims.exp - claims.iat, 900); assert.equal(claims.origin, 'desktop')
  assert.equal(claims.sub, 'owner-r11-b')
  return claims
}

try {
  await apiStarted; await waitApi()
  const viteEnv = { PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: run, LANG: 'en_US.UTF-8', VITE_R11_BASE_URL: baseUrl, VITE_R11_ACCOUNT_TOKEN: accountTokenA }
  vite = spawn(path.join(root, 'node_modules', '.bin', 'vite'), ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'], { cwd: root, env: viteEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let resolveVite, rejectVite
  const viteStarted = new Promise((resolve, reject) => { resolveVite = resolve; rejectVite = reject })
  const viteTimer = setTimeout(() => rejectVite(Error('R11 Vite preview did not start')), 20000)
  for (const stream of [vite.stdout, vite.stderr]) stream.on('data', chunk => { logs.vite += chunk.toString(); if (/Local:\s+http:\/\/127\.0\.0\.1:\d+\//.test(logs.vite)) { clearTimeout(viteTimer); resolveVite() } })
  vite.on('error', rejectVite)
  await viteStarted
  const previewUrl = `http://127.0.0.1:${vitePort}/tests/r11-identity-ollama-preview.html`
  const preview = await nativeFetch(previewUrl)
  assert.equal(preview.status, 200)
  console.log(`READY_UI_URL=${previewUrl}`)
  console.log('Use the isolated browser to confirm the local Ollama request, replay, explicit resume and logout. The runner will continue after it observes the expected browser requests.')

  // Owner B checks issuer claims and isolation without invoking the model.
  const identityB = createCloudBaseIdentityClient({ baseUrl, displayName: 'R11 owner B', getCloudBaseAccountToken: async () => accountTokenB })
  const sessionB = await identityB.exchange()
  assert.equal(sessionB.origin, 'desktop')
  const tokenB = await identityB.getUserToken()
  const responseJwks = await nativeFetch(baseUrl + '/.well-known/jwks.json')
  assert.equal(responseJwks.status, 200)
  const jwks = await responseJwks.json()
  assert.equal(JSON.stringify(jwks).includes('"d"'), false)
  await verifyJwt(tokenB, jwks)
  const clientB = createCloudClient({ baseUrl, getUserToken: identityB.getUserToken })
  const renewedSessionB = await identityB.renew()
  const renewedTokenB = await identityB.getUserToken()
  assert.equal(renewedSessionB.deviceId, sessionB.deviceId); assert.equal(renewedSessionB.ownerId, sessionB.ownerId)
  assert.notEqual(renewedTokenB, tokenB)
  const renewedClaimsB = await verifyJwt(renewedTokenB, jwks)
  const wrongOwnerRenew = await nativeFetch(baseUrl + '/v1/auth/session/renew', { method: 'POST', headers: { Authorization: `Bearer ${accountTokenA}`, 'X-Yingxu-Session': renewedTokenB, 'Content-Type': 'application/json' }, body: '{}' })
  assert.equal(wrongOwnerRenew.status, 403)
  const wrongOriginRenew = await nativeFetch(baseUrl + '/v1/auth/session/renew', { method: 'POST', headers: { Authorization: `Bearer ${accountTokenBMobile}`, 'X-Yingxu-Session': renewedTokenB, 'Content-Type': 'application/json' }, body: '{}' })
  assert.equal(wrongOriginRenew.status, 403)
  const registeredB = await clientB.registerDevice('R11 owner B')
  const devicesB = await clientB.devices()
  assert.equal(registeredB.origin, 'desktop'); assert.equal(devicesB.length, 1); assert.equal(devicesB[0].displayName, 'R11 owner B')
  const ownerBSearch = await clientB.searchBackupJobs('R11 样本备份')
  assert.equal(ownerBSearch.matches.length, 0)
  await assert.rejects(() => createCloudBaseIdentityClient({ baseUrl, displayName: 'invalid', getCloudBaseAccountToken: async () => invalidAccountToken }).exchange(), { code: 'INVALID_ACCOUNT_SESSION' })
  const missingResumeId = { sessionId: 'r11-missing-session', turnId: 'r11-missing-turn' }
  await assert.rejects(() => clientB.resumeModel('无效检查点验证', missingResumeId, { explicitConsent: true }), error => error.code === 'CHECKPOINT_NOT_FOUND' && error.automaticRetryAllowed === false)
  await identityB.logout()
  const revokedB = await nativeFetch(baseUrl + '/v1/devices', { headers: { Authorization: `Bearer ${tokenB}` } })
  assert.equal(revokedB.status, 403)

  const uiDeadline = Date.now() + 180000
  while (Date.now() < uiDeadline) {
    const observedTurns = Number((await readFile(turnCallsFile, 'utf8')).trim())
    const observedLogouts = Number((await readFile(logoutCallsFile, 'utf8')).trim())
    if (observedTurns >= 4 && observedLogouts >= 1) break
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  assert.ok(Number((await readFile(turnCallsFile, 'utf8')).trim()) >= 4, 'browser completed the model turn, replay and explicit resume')
  assert.ok(Number((await readFile(logoutCallsFile, 'utf8')).trim()) >= 1, 'browser confirmed logout and device revocation')
  const providerCalls = Number((await readFile(providerCallsFile, 'utf8')).trim())
  const toolCalls = JSON.parse(await readFile(toolCallsFile, 'utf8'))
  const identityCalls = JSON.parse(await readFile(identityCallsFile, 'utf8'))
  const agentTurnRequests = Number((await readFile(turnCallsFile, 'utf8')).trim())
  const renewalRequests = JSON.parse(await readFile(renewalCallsFile, 'utf8'))
  assert.ok(providerCalls >= 2 && providerCalls <= 3, `expected one bounded real local model/tool turn, got ${providerCalls} provider dispatches`)
  const ollamaToolCalls = toolCalls.filter(row => row.source === 'ollama_model_tool')
  assert.ok(ollamaToolCalls.length >= 1 && ollamaToolCalls.every(row => ['search_backup_jobs', 'get_backup_job'].includes(row.tool)), 'Ollama must invoke a real allowlisted read-only tool')
  assert.ok(ollamaToolCalls.some(row => row.tool === 'search_backup_jobs' && row.arguments.query === 'R11样本备份' && row.returnedJobs.some(job => job.jobId === 'r11-job-001' && job.name === 'R11样本备份' && job.state === 'success')), 'the model-selected search tool must return the seeded synthetic task')
  assert.equal(agentTurnRequests, 4, 'one real model turn, duplicate, explicit resume and missing-checkpoint recovery only')
  assert.equal(renewalRequests.length, 4, 'browser renewal, owner-B renewal, wrong-owner and wrong-origin rejection only')
  assert.ok(renewalRequests.every(row => row.hasAccountBearer && row.hasPreviousSessionHeader))
  assert.ok(identityCalls.length >= 5 && identityCalls.every(row => row.hasBearer && ['/auth/v1/user/me', '/auth/v1/token/introspect'].includes(row.path)))
  assert.ok(identityCalls.every(row => !JSON.stringify(row).includes(accountTokenA) && !JSON.stringify(row).includes(accountTokenB) && !JSON.stringify(row).includes(accountTokenBMobile)))
  assert.equal(await identityB.getUserToken(), null)
  // No synthetic account bearer or JWT may be persisted into SQLite or WAL state.
  const dbFiles = (await readdir(run)).filter(name => name.startsWith('cloud.sqlite'))
  const rawDb = Buffer.concat(await Promise.all(dbFiles.map(name => readFile(path.join(run, name)))))
  const dbText = rawDb.toString('latin1')
  assert.equal(dbText.includes(accountTokenA), false); assert.equal(dbText.includes(accountTokenB), false)
  assert.equal(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(dbText), false, 'no short session JWT persisted')
  const sourceHashesAfter = Object.fromEntries(await Promise.all(filesToHash.map(async file => [file, await digest(file)])))
  assert.deepEqual(sourceHashesAfter, sourceHashesBefore, 'cloud-service and backend worktrees remain read-only')
  const receipt = {
    mode: 'isolated FastAPI + temporary SQLite + ephemeral RSA + mocked CloudBase user/me and introspect + existing local Ollama',
    cloudbaseNetwork: 'none; urllib provider adapter was intercepted in-process', model, sessionLifetimeSeconds: 900,
    checks: { rs256SignatureVerified: true, jwksPublicOnly: true, ownerIsolation: true, invalidAccountRejected: true, missingCheckpointRejected: true, renewalSameDeviceAndNewToken: true, wrongOwnerRenewDenied: true, wrongOriginRenewDenied: true, logoutDeviceRevoked: true, localSessionCleared: true, noTokensInSQLite: true, ollamaToolCalls: ollamaToolCalls.length },
    providerCalls, agentTurnRequests, renewalRequests, toolCalls, identityProviderPaths: identityCalls.map(row => row.path), browserRequests: recordRequests,
    apiStopped: false, viteStopped: false, cloudSourceHashesUnchanged: true,
  }
  await writeFile(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2))
  console.log(`R11 completed: ${providerCalls} Ollama provider dispatches, ${toolCalls.length} real read-only tool calls, isolated identity/session logout verified.`)
} catch (error) {
  const diagnostic = (logs.api + '\n--- VITE ---\n' + logs.vite).replaceAll(accountTokenA, '[redacted]').replaceAll(accountTokenB, '[redacted]').replaceAll(accountTokenBMobile, '[redacted]').replaceAll(privateKey, '[redacted]').replaceAll(modelConfiguration, '[redacted]')
  console.error(`R11 isolated E2E failed: ${error instanceof Error ? error.message : 'unknown error'}\n${diagnostic}`)
  throw error
} finally {
  clearTimeout(apiTimer)
  globalThis.fetch = nativeFetch
  for (const child of [vite, api]) if (child && child.exitCode === null && child.signalCode === null) {
    child.kill('SIGTERM')
    await Promise.race([once(child, 'exit'), new Promise(resolve => setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); resolve() }, 5000))])
  }
  viteStopped = !!vite && (vite.exitCode !== null || vite.signalCode !== null)
  apiStopped = api.exitCode !== null || api.signalCode !== null
  const sourceHashesAfter = Object.fromEntries(await Promise.all(filesToHash.map(async file => [file, await digest(file)])))
  const existingReceipt = await readFile(path.join(run, 'receipt.json'), 'utf8').catch(() => null)
  if (existingReceipt) {
    const receipt = JSON.parse(existingReceipt); receipt.apiStopped = apiStopped; receipt.viteStopped = viteStopped
    receipt.cloudSourceHashesUnchanged = JSON.stringify(sourceHashesAfter) === JSON.stringify(sourceHashesBefore)
    await writeFile(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2))
  }
  await writeFile(path.join(run, 'sanitized-process.log'), (logs.api + '\n--- VITE ---\n' + logs.vite).replaceAll(accountTokenA, '[redacted]').replaceAll(accountTokenB, '[redacted]').replaceAll(accountTokenBMobile, '[redacted]').replaceAll(privateKey, '[redacted]').replaceAll(modelConfiguration, '[redacted]'))
  if (api) assert.equal(apiStopped, true, 'isolated FastAPI process stopped')
  if (vite) assert.equal(viteStopped, true, 'isolated Vite process stopped')
  assert.deepEqual(sourceHashesAfter, sourceHashesBefore, 'cloud-service sources were not modified')
  if (baseUrl) await assert.rejects(() => nativeFetch(baseUrl + '/healthz', { signal: AbortSignal.timeout(1000) }))
  await assert.rejects(() => nativeFetch(`http://127.0.0.1:${vitePort}/`, { signal: AbortSignal.timeout(1000) }))
}
