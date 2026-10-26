import assert from 'node:assert/strict'
import { createCloudClient, parseCloudReply } from '../evidence/cloud-module/cloud.js'
const job = { kind:'job', deviceId:'device-a', jobId:'job-1', name:'照片备份', state:'success', sequence:1, occurredAt:'2026-10-26T00:00:00Z', source:'synced_job_snapshot' }
const reply = { answer:'已同步摘要', matches:[job] }
assert.equal(parseCloudReply(reply).matches[0].kind,'job')
assert.equal('path' in parseCloudReply(reply).matches[0],false)
assert.throws(()=>parseCloudReply({answer:'old',matches:[{jobId:'1'}]}),{code:'CLOUD_CONTRACT_MISMATCH'})
assert.throws(()=>createCloudClient({baseUrl:'http://example.com',getUserToken:async()=>null}),{code:'INVALID_CLOUD_URL'})
let calls=[]; let result=reply; let status=200
const client = createCloudClient({baseUrl:'https://example.invalid',getUserToken:async()=> 'test-only-token',fetch:async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(result),{status})}})
const noAuth = createCloudClient({baseUrl:'https://example.invalid',getUserToken:async()=>null,fetch:async()=>{throw Error('must not send')}})
await assert.rejects(()=>noAuth.ask('测试'),{code:'AUTH_REQUIRED'})
await client.ask('测试'); assert.equal(calls[0].options.redirect,'error');assert.equal(calls[0].options.credentials,'omit')
result={...reply,sessionId:'session-1',turnId:'turn-1',persisted:true}
await client.ask('测试',{sessionId:'session-1',turnId:'turn-1'})
assert.equal(JSON.parse(calls.at(-1).options.body).turnId,'turn-1')
result={...reply,sessionId:'other',turnId:'turn-1',persisted:true}
await assert.rejects(()=>client.ask('测试',{sessionId:'session-1',turnId:'turn-1'}),{code:'TURN_NOT_CONFIRMED'})
result={syncRecords:true,syncAgentSessions:false,syncThumbnails:false,syncPaths:false}
assert.equal((await client.getPreferences()).syncAgentSessions,false)
result={turns:[{turnId:'t',deviceId:'d',query:'q',createdAt:'now',response:reply}],nextCursor:5,hasMore:false}
assert.equal((await client.turns('s',0)).turns[0].reply.matches[0].kind,'job')
result={turns:[],nextCursor:0,hasMore:true}; await assert.rejects(()=>client.turns('s',0),{code:'INVALID_CURSOR'})
result={detail:{code:'SESSION_DELETED'}};status=410;await assert.rejects(()=>client.ask('q'),{code:'SESSION_DELETED',status:410})
status=200;result={deletions:[{sessionId:'s',deletedAt:'now'}],nextCursor:2,hasMore:false}; assert.equal((await client.deletions()).deletions[0].sessionId,'s')
await assert.rejects(()=>client.deleteSession('s',false),{code:'CONFIRMATION_REQUIRED'})
result={sessionId:'s',deleted:true,recreationBlocked:true};await client.deleteSession('s',true)
console.log('Cloud draft adapter: 14 contract checks passed; injected fetch only, no deployed cloud or identity acceptance.')
