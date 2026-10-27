import assert from 'node:assert/strict'
import {
  confirmLocalFileAction,
  prepareLocalFileAction,
  selectLocalDirectory,
} from '../evidence/local-file-actions-module/local-file-actions.js'

const calls = []
const invoke = async (command, args = {}) => {
  calls.push({ command, args })
  if (command === 'select_directory') return { path: '/Volumes/fixture-target', purpose: args.purpose, grantId: 'grant-fixture' }
  if (command === 'agent_prepare_file_action') return {
    actionId: 'action-fixture', tool: args.tool, mediaId: args.mediaId,
    objectName: 'fixture.jpg', sourcePath: '/Volumes/fixture-source/fixture.jpg',
    targetName: args.newName || 'fixture.jpg', targetPath: '/Volumes/fixture-target/fixture.jpg',
    effect: '仅操作合成测试素材', requiresConfirmation: true, expiresAtMs: Date.now() + 60_000,
  }
  if (command === 'agent_confirm_file_action') return {
    actionId: args.actionId, tool: 'rename_item', status: args.accepted ? 'succeeded' : 'declined',
    userConfirmed: args.accepted, cloudExecutionAuthorized: false,
  }
  throw new Error(`unexpected IPC: ${command}`)
}

const grant = await selectLocalDirectory(invoke, 'destination')
assert.equal(grant.grantId, 'grant-fixture')
const preview = await prepareLocalFileAction(invoke, { tool: 'rename_item', mediaId: 'media-fixture', newName: 'renamed.jpg' })
assert.equal(preview.requiresConfirmation, true)
assert.equal(preview.targetName, 'renamed.jpg')
const receipt = await confirmLocalFileAction(invoke, preview.actionId, true)
assert.equal(receipt.status, 'succeeded')
assert.equal(receipt.cloudExecutionAuthorized, false)
await assert.rejects(
  confirmLocalFileAction(async () => ({ actionId: 'other-action', userConfirmed: true, cloudExecutionAuthorized: false, status: 'succeeded' }), preview.actionId, true),
  /回执与请求不匹配/,
)
assert.deepEqual(calls.map(call => call.command), ['select_directory', 'agent_prepare_file_action', 'agent_confirm_file_action'])
console.log('local file action IPC contract verified')
