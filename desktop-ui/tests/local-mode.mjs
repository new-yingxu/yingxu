import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const moduleDir = process.env.YINGXU_UI_TEST_MODULE_DIR
const modulePath = moduleDir ? pathToFileURL(resolve(moduleDir, 'local-mode.js')).href : '../evidence/local-mode-module/local-mode.js'
const { canEnterNativeLocalMode, canShowDevDiagnostics, canUseCloudFeatures, enterNativeLocalMode, runCloudAction } = await import(modulePath)

assert.equal(canEnterNativeLocalMode('native', true), true, 'Tauri native with local adapter can enter')
assert.equal(canEnterNativeLocalMode('mock', true), false, 'browser mock cannot enter native local mode')
assert.equal(canEnterNativeLocalMode('native', false), false, 'native without local adapter cannot enter')
assert.equal(canShowDevDiagnostics('native', true, true), true)
assert.equal(canShowDevDiagnostics('native', true, false), false, 'diagnostic JSON stays DEV-only')
assert.equal(canShowDevDiagnostics('mock', true, true), false)
assert.equal(canUseCloudFeatures(null, true), false)
assert.equal(canUseCloudFeatures({ ownerId: 'fixture' }, true), false, 'local mode always disables cloud, even with a stale session value')

{
  let session = { ownerId: 'stale-cloud-session' }
  let enabled = false
  let localLoads = 0
  let cloudCalls = 0
  const cloud = { login: async () => { cloudCalls++ }, sync: async () => { cloudCalls++ }, netdisk: async () => { cloudCalls++ } }
  const entered = await enterNativeLocalMode({
    mode: 'native', hasLocalAdapter: true,
    loadLocalData: async () => { localLoads++ },
    setSession: (value) => { session = value },
    setEnabled: (value) => { enabled = value },
  })
  assert.equal(entered, true)
  assert.equal(session, null, 'local mode does not create or retain an auth session')
  assert.equal(enabled, true)
  assert.equal(localLoads, 1)
  for (const action of [cloud.login, cloud.sync, cloud.netdisk]) {
    assert.equal(await runCloudAction(session, enabled, action), false)
  }
  assert.equal(cloudCalls, 0, 'local mode blocks login, cloud sync and netdisk actions')
}

{
  let localLoads = 0
  const entered = await enterNativeLocalMode({
    mode: 'mock', hasLocalAdapter: true,
    loadLocalData: async () => { localLoads++ }, setSession: () => {}, setEnabled: () => {},
  })
  assert.equal(entered, false)
  assert.equal(localLoads, 0, 'browser mock cannot initialize the native workspace')
}

console.log('10 native local-mode checks passed; no network/cloud calls made.')
