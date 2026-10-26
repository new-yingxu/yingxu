import { readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import assert from 'node:assert/strict'
const raw=await readFile(process.argv[2]);const api=JSON.parse(raw)
const endpoints={
 '/v1/account/sync-preferences':['get','put'],
 '/v1/agent/turns':['post'],
 '/v1/agent/model':['get'],
 '/v1/agent/sessions/{session_id}/turns':['get'],
 '/v1/agent/sessions/{session_id}':['delete'],
 '/v1/agent/session-deletions':['get'],
 '/v1/sync/invalidations':['get'],
 '/v1/sync/snapshot':['get'],
}
for(const [path,methods] of Object.entries(endpoints))for(const method of methods)assert.ok(api.paths[path]?.[method],`${method} ${path}`)
for(const path of ['/v1/agent/session-deletions','/v1/sync/invalidations','/v1/sync/snapshot','/v1/agent/sessions/{session_id}/turns'])assert.ok(api.paths[path].get.parameters.some(p=>p.name==='after_id'&&p.in==='query'))
const schemas=api.components.schemas
assert.equal(schemas.AgentTurnRequest.properties.useModel.default,false);assert.equal(schemas.AgentTurnRequest.properties.useModel.type,'boolean');
assert.ok(schemas.AgentTurnRequest.properties.sessionId);assert.ok(schemas.AgentTurnRequest.properties.turnId)
assert.equal(schemas.SessionDeletion.properties.confirmed.const,true)
for(const key of ['syncRecords','syncAgentSessions','syncThumbnails','syncPaths'])assert.ok(schemas.SyncPreferencesUpdate.required.includes(key))
const receipt={openapiSha256:createHash('sha256').update(raw).digest('hex'),endpoints,requestChecks:'passed',responseSchemas:'untyped for these endpoints; response shape checked against current main.py and synthetic fixtures, not OpenAPI response proof',deployedAcceptance:false}
await writeFile(new URL('../evidence/r6-openapi-receipt.json',import.meta.url),JSON.stringify(receipt,null,2));console.log('OpenAPI route/query/request-field checks passed; response schemas are untyped, deployment not checked.')
