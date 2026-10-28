// Local development-auth HTTP acceptance. No production login or CloudBase gateway.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomBytes, createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import { createCloudClient, createOwnerSync, emptyOwnerCache } from '../evidence/r4-module/r4-entry.js'
const cloudRoot=process.argv[2], python=process.argv[3]
if(!cloudRoot||!python)throw Error('Usage: node tests/cloud-http-r5.mjs <cloud-worktree-root> <python-path>')
const evidence=fileURLToPath(new URL('../evidence/',import.meta.url))
await mkdir(evidence,{recursive:true})
const run=await mkdtemp(path.join(evidence,'r5-http-'))
const token=randomBytes(32).toString('base64url') // only memory and the child's environment; never logged or saved.
const inputs=['app/main.py','app/store.py','app/config.py','app/auth.py']
const hashInputs=async()=>Object.fromEntries(await Promise.all(inputs.map(async f=>[f,createHash('sha256').update(await readFile(path.join(cloudRoot,'cloud-service',f))).digest('hex')])))
const before=await hashInputs()
const environment={...process.env,APP_ENV:'development',AUTH_MODE:'dev',DEV_AUTH_TOKEN:token,DATABASE_URL:`sqlite:///${path.join(run,'cloud.sqlite')}`,APP_VERSION:'r5-local-http',CLOUDBASE_ENV_ID:'',CLOUDBASE_APIKEY:'',JWT_ISSUER:'',JWT_AUDIENCE:'',JWT_JWKS_URL:'',PYTHONDONTWRITEBYTECODE:'1'}
const child=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','0','--no-access-log'],{cwd:path.join(cloudRoot,'cloud-service'),env:environment,stdio:['ignore','pipe','pipe']})
let serverLog='',baseUrl,stopped=false
const requests=[], checks=[]
let rejectStart,resolveStart
const start=new Promise((resolve,reject)=>{resolveStart=resolve;rejectStart=reject})
const startupTimer=setTimeout(()=>rejectStart(Error('Local service startup timed out')),20000)
for(const stream of [child.stdout,child.stderr])stream.on('data',b=>{
 serverLog+=b.toString()
 const match=serverLog.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/)
 if(match){baseUrl=match[1];clearTimeout(startupTimer);resolveStart(baseUrl)}
})
child.on('error',rejectStart);child.on('exit',code=>{if(!baseUrl)rejectStart(Error(`Local service exited before startup (${code})`))})
class Store {
 rows=new Map()
 async read(owner){return structuredClone(this.rows.get(owner)??emptyOwnerCache(owner))}
 async transaction(owner,change){const draft=await this.read(owner);change(draft);this.rows.set(owner,draft)}
}
async function check(name,work){await work();checks.push(name);console.log(`PASS ${name}`)}
let scope
try{
 await start
 const transport=async(url,options)=>{
   assert.equal(new URL(url).origin,baseUrl,'HTTP must remain inside this isolated service')
   const r=await fetch(url,options);requests.push({method:options?.method??'GET',path:new URL(url).pathname,status:r.status});return r
 }
 const api=createCloudClient({baseUrl,getUserToken:async()=>token,fetch:transport})
 async function raw(route,method='GET',body){
   const r=await transport(baseUrl+route,{method,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
   const result=await r.json();assert.ok(r.ok,`Local ${method} ${route}: ${r.status} ${JSON.stringify(result)}`);return result
 }
 await check('readyz confirms isolated SQLite and explicit dev auth',async()=>{const r=await raw('/readyz');assert.equal(r.ready,true);assert.equal(r.version,'r5-local-http')})
 await check('unregistered device preserves real 403 error',async()=>{await assert.rejects(()=>api.getPreferences(),{code:'DEVICE_REVOKED_OR_UNREGISTERED',status:403})})
 await check('register actual development principal device',async()=>{const r=await api.registerDevice('R5 isolated desktop HTTP');assert.equal(r.deviceId,'local-dev-device');assert.equal(r.origin,'desktop');const rows=await api.devices();assert.equal(rows.length,1);assert.equal(rows[0].deviceId,r.deviceId);assert.equal(rows[0].revoked,false)})
 const prefs={syncRecords:true,syncAgentSessions:true,syncThumbnails:false,syncPaths:true}
 await check('get and update exact sync preference aliases',async()=>{const defaults=await api.getPreferences();assert.equal(defaults.syncPaths,false);assert.deepEqual(await api.setPreferences(prefs),prefs)})
 const event=(n)=>({schemaVersion:1,eventId:`r5-event-${n}`,deviceId:'local-dev-device',sequence:n,eventType:'job.snapshot.v1',occurredAt:new Date().toISOString(),payload:{jobId:`r5-job-${n}`,name:`R5备份任务${n}`,state:'success',phase:'complete',files:1,bytes:n,progress:100,targets:[{id:'r5-target',state:'success',copied:1,skipped:0,verified:1,failed:0}],source:'/r5-synthetic/source',paths:['/r5-synthetic/target']}})
 const events=Array.from({length:101},(_,i)=>event(i+1))
 await check('ingest real schema-valid events in allowed 100+1 batches',async()=>{const a=await raw('/v1/sync/events','POST',{events:events.slice(0,100)}),b=await raw('/v1/sync/events','POST',{events:events.slice(100)});assert.equal(a.acceptedEventIds.length,100);assert.equal(b.acceptedEventIds.length,1)})
 await check('real snapshot pagination 100 then 1 with server cursor',async()=>{const a=await api.snapshot();assert.equal(a.events.length,100);assert.equal(a.hasMore,true);const b=await api.snapshot(a.nextCursor);assert.equal(b.events.length,1);assert.equal(b.hasMore,false);assert.ok(b.nextCursor>a.nextCursor)})
 await check('Agent real HTTP returns typed job summaries',async()=>{const r=await api.ask('R5备份',{sessionId:'r5-session',turnId:'turn-0'});assert.equal(r.historySync,'synced');assert.equal(r.matches.length,50);assert.equal(r.matches[0].kind,'job');assert.equal(r.matches[0].deviceId,'local-dev-device');assert.equal('path' in r.matches[0],false)})
 await check('Agent stored turns paginate across 100 boundary',async()=>{for(let i=1;i<=100;i++)await api.ask('R5备份',{sessionId:'r5-session',turnId:`turn-${i}`});const a=await api.turns('r5-session');assert.equal(a.turns.length,100);assert.equal(a.hasMore,true);const b=await api.turns('r5-session',a.nextCursor);assert.equal(b.turns.length,1);assert.equal(b.hasMore,false);assert.equal(b.turns[0].reply.matches[0].kind,'job')})
 const store=new Store()
 scope=createOwnerSync('local-dev-owner',store,signal=>createCloudClient({baseUrl,getUserToken:async()=>token,fetch:transport,signal}))
 await check('cache coordinator consumes actual HTTP snapshot pages',async()=>{await scope.sync();const s=await scope.view();assert.equal(s.tasks.size,101);assert.ok(s.cursors.tasks>=101);assert.equal(s.cursors.deletions,0);assert.equal(s.cursors.invalidations,0);assert.equal([...s.tasks.values()][0].payload.source,'/r5-synthetic/source')})
 await check('legitimate persisted=false answer stays available and not in history',async()=>{await api.setPreferences({...prefs,syncAgentSessions:false});const r=await api.ask('R5备份',{sessionId:'r5-unsynced',turnId:'u-1'});assert.equal(r.historySync,'not_synced');assert.ok(r.text);assert.equal((await api.turns('r5-unsynced')).turns.length,0);await scope.enqueue({sessionId:'r5-unsynced',turnId:'u-2',query:'R5备份'});const queued=await scope.flushOne();assert.equal(queued.historySync,'not_synced');assert.equal((await store.read('local-dev-owner')).turns.size,0)})
 await check('actual paths_redacted notification cleans all cached source/paths',async()=>{await api.setPreferences({...prefs,syncAgentSessions:false,syncPaths:false});const notices=await api.invalidations();assert.equal(notices.invalidations[0].kind,'paths_redacted');await scope.sync();const s=await scope.view();assert.ok(s.cursors.invalidations>0);assert.equal(s.cursors.deletions,0);for(const j of s.tasks.values()){assert.equal(j.payload.source,undefined);assert.equal(j.payload.paths,undefined)}})
 await check('delete real session makes old and new turns return 410',async()=>{await api.deleteSession('r5-session',true);await assert.rejects(()=>api.ask('R5备份',{sessionId:'r5-session',turnId:'turn-0'}),{code:'SESSION_DELETED',status:410});await assert.rejects(()=>api.ask('R5备份',{sessionId:'r5-session',turnId:'new-turn'}),{code:'SESSION_DELETED',status:410});assert.equal((await api.turns('r5-session')).turns.length,0)})
 await check('real deletion notice removes pending replay and advances only deletion cursor',async()=>{await scope.enqueue({sessionId:'r5-session',turnId:'offline-turn',query:'R5备份'});const prior=await store.read('local-dev-owner');await scope.sync();const s=await scope.view();assert.ok(s.cursors.deletions>0);assert.equal(s.cursors.tasks,prior.cursors.tasks);assert.equal(s.cursors.invalidations,prior.cursors.invalidations);assert.equal(s.pending.size,0);assert.ok(s.deletedSessions.has('r5-session'));await assert.rejects(()=>scope.enqueue({sessionId:'r5-session',turnId:'replay',query:'R5备份'}),{code:'SESSION_DELETED'})})
 await check('actual 410 during queued send creates local tombstone without swallowing error',async()=>{
   const race=createOwnerSync('local-dev-owner',new Store(),signal=>createCloudClient({baseUrl,getUserToken:async()=>token,fetch:async(url,opts)=>{
     // Real requests, deterministic race: delete after fetching deletion notices, before /turns POST.
     if(new URL(url).pathname==='/v1/agent/turns')await api.deleteSession('r5-race',true)
     return transport(url,opts)
   },signal}))
   await race.enqueue({sessionId:'r5-race',turnId:'late',query:'R5备份'});await assert.rejects(()=>race.flushOne(),{code:'SESSION_DELETED',status:410});await assert.rejects(()=>race.enqueue({sessionId:'r5-race',turnId:'new',query:'R5备份'}),{code:'SESSION_DELETED'});race.stop()
 })
 await check('stopped scope refuses new queued work',async()=>{scope.stop();await assert.rejects(()=>scope.enqueue({sessionId:'new-session',turnId:'t',query:'R5备份'}),{code:'OWNER_SESSION_STOPPED'})})
 console.log(`${checks.length} local FastAPI development-auth HTTP checks passed.`)
}finally{
 clearTimeout(startupTimer);scope?.stop()
 if(child.exitCode===null){child.kill('SIGTERM');await Promise.race([once(child,'exit'),new Promise(r=>setTimeout(()=>{if(child.exitCode===null)child.kill('SIGKILL');r()},5000))])}
 stopped=child.exitCode!==null||child.signalCode!==null
 await writeFile(path.join(run,'server.log'),serverLog.replaceAll(token,'[redacted]'))
 await writeFile(path.join(run,'receipt.json'),JSON.stringify({mode:'local FastAPI + SQLite + explicit dev auth; NOT production identity/cloud acceptance',command:[python,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','0','--no-access-log'],environment:{APP_ENV:'development',AUTH_MODE:'dev',DEV_AUTH_TOKEN:'randomly generated in memory, not persisted',DATABASE_URL:'isolated cloud.sqlite',PYTHONDONTWRITEBYTECODE:'1'},baseUrl,checks,requests,serverStopped:stopped,inputsBefore:before,inputsAfter:await hashInputs()},null,2))
 await writeFile(path.join(evidence,'r5-http-latest.json'),JSON.stringify({run,passed:checks.length,serverStopped:stopped},null,2))
 if(baseUrl){await assert.rejects(()=>fetch(baseUrl+'/healthz',{signal:AbortSignal.timeout(1000)}));console.log('Owned FastAPI stopped; loopback port closed.')}
}
