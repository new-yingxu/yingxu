// Isolated loopback acceptance for the local LangGraph 0.4 preview; no CloudBase or real model credentials.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomBytes, createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import { createCloudClient } from '../evidence/r9-module/cloud.js'

const cloudRoot = process.argv[2], python = process.argv[3]
if (!cloudRoot || !python) throw Error('Usage: node tests/cloud-http-r9.mjs <cloud-worktree-root> <python-path>')
const evidence = fileURLToPath(new URL('../evidence/', import.meta.url))
await mkdir(evidence, { recursive: true })
const run = await mkdtemp(path.join(evidence, 'r9-http-'))
const token = randomBytes(32).toString('base64url')
const providerMarker = path.join(run, 'provider-call-count.txt')
await writeFile(providerMarker, '0')
const inputs = ['app/main.py', 'app/store.py', 'app/config.py', 'app/auth.py', 'app/agent_graph.py', 'app/agent_tools.py', 'app/model_provider.py']
const hashInputs = async () => Object.fromEntries(await Promise.all(inputs.map(async file => [file, createHash('sha256').update(await readFile(path.join(cloudRoot, 'cloud-service', file))).digest('hex')])))
const before = await hashInputs()
const environment = {
  PATH: '/tmp/yingxu-cloud-py311/bin:/usr/bin:/bin',
  HOME: run,
  LANG: 'en_US.UTF-8',
  APP_ENV: 'development', AUTH_MODE: 'dev', DEV_AUTH_TOKEN: token,
  DATABASE_URL: `sqlite:///${path.join(run, 'cloud.sqlite')}`,
  APP_VERSION: '0.4.0-langgraph-preview-r9-local',
  CLOUDBASE_ENV_ID: '', CLOUDBASE_APIKEY: '',
  JWT_ISSUER: '', JWT_AUDIENCE: '', JWT_JWKS_URL: '',
  MODEL_OWNER_CREDENTIALS: '',
  HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '', http_proxy: '', https_proxy: '', all_proxy: '',
  NO_PROXY: '*', no_proxy: '*', PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
  R9_PROVIDER_MARKER: providerMarker,
}
const serverCode = String.raw`
import os
from pathlib import Path
import uvicorn
from app import main
calls = []
def provider_must_not_run(*args, **kwargs):
    calls.append('called')
    raise RuntimeError('unexpected provider dispatch in unconfigured test')
main.generate_answer = provider_must_not_run
try:
    uvicorn.run(main.app, host='127.0.0.1', port=0, log_level='info', access_log=False)
finally:
    Path(os.environ['R9_PROVIDER_MARKER']).write_text(str(len(calls)))
`
const child = spawn(python, ['-c', serverCode], { cwd: path.join(cloudRoot, 'cloud-service'), env: environment, stdio: ['ignore', 'pipe', 'pipe'] })
let serverLog = '', baseUrl, serverStopped = false
const requests = [], checks = []
let resolveStart, rejectStart
const started = new Promise((resolve, reject) => { resolveStart = resolve; rejectStart = reject })
const timer = setTimeout(() => rejectStart(Error('Local LangGraph service startup timed out')), 20000)
for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => {
  serverLog += chunk.toString()
  const match = serverLog.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/)
  if (match) { baseUrl = match[1]; clearTimeout(timer); resolveStart(baseUrl) }
})
child.on('error', rejectStart)
child.on('exit', code => { if (!baseUrl) rejectStart(Error(`Local service exited before startup (${code})`)) })
const verify = async (name, work) => { await work(); checks.push(name); console.log(`PASS ${name}`) }
let api
try {
  await started
  const transport = async (url, options) => {
    assert.equal(new URL(url).origin, baseUrl, 'all frontend HTTP requests must stay on this isolated loopback service')
    const response = await fetch(url, options)
    requests.push({ method: options?.method ?? 'GET', path: new URL(url).pathname, status: response.status })
    return response
  }
  api = createCloudClient({ baseUrl, getUserToken: async () => token, fetch: transport })
  const raw = async (route, method = 'GET', body) => {
    const response = await transport(baseUrl + route, {
      method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return { status: response.status, body: await response.json() }
  }
  const waitReady = async () => {
    const end = Date.now() + 10000
    while (Date.now() < end) {
      try { const response = await raw('/readyz'); if (response.status === 200 && response.body.ready) return response.body }
      catch { /* local startup readiness only */ }
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    throw Error('local /readyz did not become ready')
  }
  await verify('0.4 LangGraph FastAPI ready on isolated SQLite and development auth', async () => {
    const ready = await waitReady()
    assert.equal(ready.version, '0.4.0-langgraph-preview-r9-local')
  })
  let device
  await verify('register isolated local development device', async () => {
    device = await api.registerDevice('R9 isolated desktop')
    assert.equal(device.deviceId, 'local-dev-device')
    assert.equal(device.origin, 'desktop')
  })
  const event = (sequence, jobId, name) => ({
    schemaVersion: 1, eventId: `r9-event-${sequence}`, deviceId: device.deviceId, sequence,
    eventType: 'job.snapshot.v1', occurredAt: new Date().toISOString(),
    payload: { jobId, name, state: 'success', phase: 'complete', files: 1, bytes: 12, progress: 100,
      targets: [{ id: 'r9-local-target', state: 'success', copied: 1, skipped: 0, verified: 1, failed: 0 }] },
  })
  await verify('seed only synthetic owner-scoped job snapshots through local HTTP', async () => {
    const result = await raw('/v1/sync/events', 'POST', { events: [event(1, 'r9-job-1', 'R9 Sample Backup'), event(2, 'r9-job-2', 'R9 Archive')] })
    assert.equal(result.status, 200, JSON.stringify(result.body))
    assert.equal(result.body.acceptedEventIds.length, 2)
  })
  let directory
  await verify('tool directory identifies LangGraph and separates read tools from proposal-only writes', async () => {
    directory = await api.toolDirectory()
    assert.equal(directory.orchestrator, 'langgraph')
    assert.equal(directory.executionAuthorized, false)
    for (const name of ['search_backup_jobs', 'get_backup_job']) {
      const item = directory.tools.find(tool => tool.name === name)
      assert.equal(item?.permission, 'read')
      assert.equal(item?.executableInCloud, true)
    }
    for (const name of ['rename_item', 'move_item', 'upload_baidu']) {
      const item = directory.tools.find(tool => tool.name === name)
      assert.equal(item?.permission, 'proposal_only')
      assert.equal(item?.executableInCloud, false)
      assert.equal(item?.requiresLocalConfirmation, true)
    }
  })
  await verify('frontend adapter has no generic tool executor and rejects owner injection before HTTP', async () => {
    assert.equal(api.invokeTool, undefined)
    const count = requests.length
    await assert.rejects(() => api.searchBackupJobs({ query: 'R9', owner: 'other-owner' }), { code: 'INVALID_QUERY' })
    await assert.rejects(() => api.getBackupJob({ jobId: 'r9-job-1', owner: 'other-owner' }), { code: 'INVALID_ID' })
    assert.equal(requests.length, count)
  })
  await verify('normal turn returns LangGraph orchestration marker and synced task summary', async () => {
    const reply = await api.ask('R9 Sample Backup', { sessionId: 'r9-session', turnId: 'r9-turn-1' })
    assert.equal(reply.orchestrator, 'langgraph')
    assert.equal(reply.historySync, 'synced')
    assert.equal(reply.matches[0]?.kind, 'job')
    assert.equal('path' in reply.matches[0], false)
  })
  await verify('typed read-only search tool invokes the advertised cloud-search tool', async () => {
    const result = await api.searchBackupJobs('R9 Sample')
    assert.equal(result.executionAuthorized, false)
    assert.equal(result.dataSource, 'owner_scoped_sync_events')
    assert.equal(result.searchWindow.eventLimit, 200)
    assert.equal(result.searchWindow.complete, false)
    assert.deepEqual(result.matches.map(row => row.jobId), ['r9-job-1'])
    assert.equal('path' in result.matches[0], false)
  })
  await verify('typed lookup returns a real owner-scoped match', async () => {
    const result = await api.getBackupJob('r9-job-2', device.deviceId)
    assert.equal(result.found, true)
    assert.equal(result.reason, null)
    assert.equal(result.jobs[0]?.jobId, 'r9-job-2')
    assert.equal(result.executionAuthorized, false)
  })
  await verify('lookup miss is explicitly bounded to NOT_IN_SEARCH_WINDOW', async () => {
    const result = await api.getBackupJob('job-outside-window')
    assert.equal(result.found, false)
    assert.equal(result.reason, 'NOT_IN_SEARCH_WINDOW')
    assert.equal(result.jobs.length, 0)
  })
  await verify('owner argument injection is rejected for both registered read tools', async () => {
    const search = await raw('/v1/agent/tools/search_backup_jobs', 'POST', { arguments: { query: 'R9', owner: 'other-owner' } })
    const lookup = await raw('/v1/agent/tools/get_backup_job', 'POST', { arguments: { jobId: 'r9-job-1', owner: 'other-owner' } })
    assert.equal(search.status, 422)
    assert.equal(lookup.status, 422)
  })
  await verify('write and unknown tool calls are refused by the server', async () => {
    for (const name of ['rename_item', 'move_item', 'upload_baidu', 'not_a_real_tool']) {
      const result = await raw(`/v1/agent/tools/${name}`, 'POST', { arguments: {} })
      assert.equal(result.status, 403, `${name} must not execute in the cloud`)
      assert.equal(result.body.detail.code, 'TOOL_NOT_ALLOWED')
    }
  })
  await verify('unconfigured model status is explicit and model request returns 503 without provider dispatch', async () => {
    const status = await api.modelStatus()
    assert.deepEqual(status, { configured: false, requiresExplicitConsent: true, executionAuthorized: false })
    await assert.rejects(() => api.askModel('R9 model not configured', { sessionId: 'r9-model-session', turnId: 'r9-model-turn' }, { explicitConsent: true }), error =>
      error.code === 'MODEL_NOT_CONFIGURED' && error.status === 503 && error.automaticRetryAllowed === false && error.modelDispatched === false)
  })
  const calls = Number((await readFile(providerMarker, 'utf8')).trim())
  assert.equal(calls, 0, 'model provider callback must not run without owner credentials')
  assert.ok(requests.every(row => row.status >= 200 && row.status < 500 || row.status === 503))
  console.log(`${checks.length} R9 local HTTP checks passed; provider dispatch count ${calls}.`)
} finally {
  clearTimeout(timer)
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGTERM')
    await Promise.race([once(child, 'exit'), new Promise(resolve => setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); resolve() }, 5000))])
  }
  serverStopped = child.exitCode !== null || child.signalCode !== null
  let providerCalls = null
  try { providerCalls = Number((await readFile(providerMarker, 'utf8')).trim()) } catch { /* process startup failure only */ }
  await writeFile(path.join(run, 'server.log'), serverLog.replaceAll(token, '[redacted]'))
  const after = await hashInputs()
  const receipt = {
    mode: 'local FastAPI 0.4 LangGraph + isolated SQLite + random development bearer; not production identity or CloudBase',
    python, appVersion: '0.4.0-langgraph-preview-r9-local',
    environment: { APP_ENV: 'development', AUTH_MODE: 'dev', DEV_AUTH_TOKEN: 'random in memory only', DATABASE_URL: 'isolated cloud.sqlite', MODEL_OWNER_CREDENTIALS: 'unset', CLOUDBASE_ENV_ID: 'unset', CLOUDBASE_APIKEY: 'unset', outbound_proxy: 'disabled' },
    baseUrl, checks, requests, providerCalls, serverStopped, inputsBefore: before, inputsAfter: after,
  }
  await writeFile(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2))
  await writeFile(path.join(evidence, 'r9-http-latest.json'), JSON.stringify({ run, passed: checks.length, providerCalls, serverStopped }, null, 2))
  assert.equal(serverStopped, true, 'owned local server must be stopped')
  assert.deepEqual(after, before, 'cloud worktree sources must remain read-only')
  if (baseUrl) await assert.rejects(() => fetch(baseUrl + '/readyz', { signal: AbortSignal.timeout(1000) }))
  console.log('Owned local FastAPI stopped; isolated loopback port is closed; cloud worktree hashes unchanged.')
}
