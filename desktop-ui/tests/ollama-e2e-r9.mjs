// Starts an isolated local LangGraph API and Vite consent preview, then waits for CUA user-authorized interaction.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomBytes, createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import readline from 'node:readline'

const cloudRoot = process.argv[2], python = process.argv[3]
if (!cloudRoot || !python) throw Error('Usage: node tests/ollama-e2e-r9.mjs <cloud-worktree-root> <python-path>')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const evidence = path.join(root, 'evidence')
await mkdir(evidence, { recursive: true })
const run = await mkdtemp(path.join(evidence, 'r9-ollama-'))
const token = randomBytes(32).toString('base64url')
const providerMarker = path.join(run, 'provider-call-count.txt')
const turnRequestMarker = path.join(run, 'agent-turn-request-count.txt')
await writeFile(providerMarker, '0')
await writeFile(turnRequestMarker, '0')
let vitePort
const portServer = createServer()
await new Promise(resolve => portServer.listen(0, '127.0.0.1', resolve))
vitePort = portServer.address().port
await new Promise((resolve, reject) => portServer.close(error => error ? reject(error) : resolve()))
const model = 'qwen2.5:7b'
const modelConfig = JSON.stringify({ 'local-dev-owner': { provider: 'ollama', model } })
const inputs = ['app/main.py', 'app/store.py', 'app/config.py', 'app/auth.py', 'app/agent_graph.py', 'app/agent_tools.py', 'app/model_provider.py']
const hashInputs = async () => Object.fromEntries(await Promise.all(inputs.map(async file => [file, createHash('sha256').update(await readFile(path.join(cloudRoot, 'cloud-service', file))).digest('hex')])))
const before = await hashInputs()
const pythonEnv = {
  PATH: '/tmp/yingxu-cloud-py311/bin:/usr/bin:/bin', HOME: run, LANG: 'en_US.UTF-8',
  APP_ENV: 'development', AUTH_MODE: 'dev', DEV_AUTH_TOKEN: token, CORS_ORIGINS: `http://127.0.0.1:${vitePort}`,
  DATABASE_URL: `sqlite:///${path.join(run, 'cloud.sqlite')}`, APP_VERSION: '0.4.0-langgraph-preview-r9-ollama-local',
  CLOUDBASE_ENV_ID: '', CLOUDBASE_APIKEY: '', JWT_ISSUER: '', JWT_AUDIENCE: '', JWT_JWKS_URL: '',
  MODEL_OWNER_CREDENTIALS: modelConfig,
  HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '', http_proxy: '', https_proxy: '', all_proxy: '', NO_PROXY: '*', no_proxy: '*',
  PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1', R9_PROVIDER_MARKER: providerMarker, R9_TURN_REQUEST_MARKER: turnRequestMarker,
}
const serverCode = String.raw`
import os
from pathlib import Path
import uvicorn
from app import main
original = main.generate_answer
turn_marker = Path(os.environ['R9_TURN_REQUEST_MARKER'])
class CountAgentTurnRequests:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and scope.get('method') == 'POST' and scope.get('path') == '/v1/agent/turns':
            count = int(turn_marker.read_text()) + 1
            turn_marker.write_text(str(count))
        await self.app(scope, receive, send)
def counted_provider(*args, **kwargs):
    marker = Path(os.environ['R9_PROVIDER_MARKER'])
    count = int(marker.read_text()) + 1
    marker.write_text(str(count))
    return original(*args, **kwargs)
main.generate_answer = counted_provider
uvicorn.run(CountAgentTurnRequests(main.app), host='127.0.0.1', port=0, log_level='info', access_log=False)
`
const api = spawn(python, ['-c', serverCode], { cwd: path.join(cloudRoot, 'cloud-service'), env: pythonEnv, stdio: ['ignore', 'pipe', 'pipe'] })
const logs = { api: '', vite: '' }
let baseUrl, apiPort, apiStopped = false, viteStopped = false
const requests = []
let resolveApi, rejectApi
const apiStarted = new Promise((resolve, reject) => { resolveApi = resolve; rejectApi = reject })
const timer = setTimeout(() => rejectApi(Error('Local Ollama LangGraph API startup timed out')), 20000)
for (const stream of [api.stdout, api.stderr]) stream.on('data', chunk => {
  logs.api += chunk.toString()
  const match = logs.api.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/)
  if (match) { baseUrl = match[1]; clearTimeout(timer); resolveApi(baseUrl) }
})
api.on('error', rejectApi)
api.on('exit', code => { if (!baseUrl) rejectApi(Error(`Local API exited before startup (${code})`)) })
let vite
try {
  await apiStarted
  const apiTransport = async (url, options) => {
    assert.equal(new URL(url).origin, baseUrl, 'cloud adapter traffic must stay inside the local FastAPI service')
    const response = await fetch(url, options)
    requests.push({ method: options?.method ?? 'GET', path: new URL(url).pathname, status: response.status })
    return response
  }
  const raw = async (route, method = 'GET', body) => {
    const response = await apiTransport(baseUrl + route, {
      method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return { status: response.status, body: await response.json() }
  }
  const waitReady = async () => {
    const end = Date.now() + 10000
    while (Date.now() < end) {
      try { const response = await raw('/readyz'); if (response.status === 200 && response.body.ready) return }
      catch { /* wait for isolated loopback startup */ }
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    throw Error('local /readyz did not become ready')
  }
  await waitReady()
  const device = await raw('/v1/devices/register', 'POST', { displayName: 'R9 local Ollama consent test' })
  assert.equal(device.status, 200)
  const state = await raw('/v1/agent/model')
  assert.deepEqual(state.body, { configured: true, provider: 'ollama', model, region: 'local', requiresExplicitConsent: true, executionAuthorized: false })

  const viteEnv = {
    PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: run, LANG: 'en_US.UTF-8',
    VITE_R9_BASE_URL: baseUrl, VITE_R9_DEV_TOKEN: token,
  }
  vite = spawn(path.join(root, 'node_modules', '.bin', 'vite'), ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'], { cwd: root, env: viteEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let resolveVite, rejectVite
  const viteStarted = new Promise((resolve, reject) => { resolveVite = resolve; rejectVite = reject })
  const viteTimer = setTimeout(() => rejectVite(Error('Local consent preview startup timed out')), 20000)
  for (const stream of [vite.stdout, vite.stderr]) stream.on('data', chunk => {
    logs.vite += chunk.toString()
    if (/Local:\s+http:\/\/127\.0\.0\.1:\d+\//.test(logs.vite)) { clearTimeout(viteTimer); resolveVite() }
  })
  vite.on('error', rejectVite)
  await viteStarted
  const url = `http://127.0.0.1:${vitePort}/tests/r9-ollama-preview.html`
  const html = await fetch(url)
  assert.equal(html.status, 200)
  const js = await fetch(`http://127.0.0.1:${vitePort}/tests/r9-ollama-preview.ts`)
  assert.equal(js.status, 200)
  console.log(`READY_UI_URL=${url}`)
  console.log('Model status is ollama/local; confirm once in the consent dialog, then use the explicit same-turn idempotency button.')
  console.log('Press Enter after the visible UI shows the reply and duplicate=true.')
  for await (const _ of readline.createInterface({ input: process.stdin })) break
  const providerCalls = Number((await readFile(providerMarker, 'utf8')).trim())
  const appTurnRequests = Number((await readFile(turnRequestMarker, 'utf8')).trim())
  assert.equal(providerCalls, 1, 'one explicit consent should cause one local Ollama model call, including same-turn replay')
  assert.equal(appTurnRequests, 2, 'one consented model request plus same-turn duplicate verification')
} finally {
  clearTimeout(timer)
  if (vite && vite.exitCode === null && vite.signalCode === null) {
    vite.kill('SIGTERM')
    await Promise.race([once(vite, 'exit'), new Promise(resolve => setTimeout(() => { if (vite.exitCode === null) vite.kill('SIGKILL'); resolve() }, 5000))])
  }
  if (api.exitCode === null && api.signalCode === null) {
    api.kill('SIGTERM')
    await Promise.race([once(api, 'exit'), new Promise(resolve => setTimeout(() => { if (api.exitCode === null) api.kill('SIGKILL'); resolve() }, 5000))])
  }
  viteStopped = !!vite && (vite.exitCode !== null || vite.signalCode !== null)
  apiStopped = api.exitCode !== null || api.signalCode !== null
  let providerCalls = null
  try { providerCalls = Number((await readFile(providerMarker, 'utf8')).trim()) } catch { /* startup failure */ }
  let appTurnRequests = null
  try { appTurnRequests = Number((await readFile(turnRequestMarker, 'utf8')).trim()) } catch { /* startup failure */ }
  const after = await hashInputs()
  await writeFile(path.join(run, 'server.log'), (logs.api + '\n--- VITE ---\n' + logs.vite).replaceAll(token, '[redacted]').replaceAll(modelConfig, '[redacted]'))
  await writeFile(path.join(run, 'receipt.json'), JSON.stringify({
    mode: 'local Ollama + isolated FastAPI/LangGraph + temporary SQLite + random in-memory development bearer',
    model: 'qwen2.5:7b', provider: 'ollama', region: 'local', modelConfiguration: 'runtime environment only; not persisted',
    confirmation: 'ModelConsent UI explicit per-turn confirmation; same-turn duplicate replay tested after consent',
    providerCalls, appTurnRequests, requests, apiStopped, viteStopped, cloudWorktreeHashesUnchanged: JSON.stringify(after) === JSON.stringify(before),
    sourceHashesBefore: before, sourceHashesAfter: after,
  }, null, 2))
  assert.equal(apiStopped, true)
  assert.equal(viteStopped, true)
  assert.deepEqual(after, before, 'cloud worktree source remains read-only')
  if (baseUrl) await assert.rejects(() => fetch(baseUrl + '/readyz', { signal: AbortSignal.timeout(1000) }))
  if (vitePort) await assert.rejects(() => fetch(`http://127.0.0.1:${vitePort}/`, { signal: AbortSignal.timeout(1000) }))
  console.log(`R9 Ollama preview stopped; provider dispatches: ${providerCalls}; temporary loopback servers closed.`)
}
