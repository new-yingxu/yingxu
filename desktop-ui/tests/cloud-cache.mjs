import assert from 'node:assert/strict'
import { createOwnerSync, emptyOwnerCache, createCloudClient, CloudError, parseCloudReply } from '../evidence/r4-module/r4-entry.js'
let passed=0
async function test(name, run) { await run(); passed++; console.log(`PASS ${name}`) }
class Store {
  rows=new Map(); fail=null
  async read(owner){return structuredClone(this.rows.get(owner)??emptyOwnerCache(owner))}
  async transaction(owner,change){const draft=await this.read(owner);change(draft);if(this.fail?.(draft))throw Error('simulated atomic rollback');this.rows.set(owner,draft)}
}
const job=(deviceId='d',sequence=1)=>({deviceId,sequence,occurredAt:'2026-10-26T00:00:00Z',payload:{jobId:'j',name:'照片',state:'success',source:'/private/card',paths:['/private/output']}})
const page=(field,rows=[],nextCursor=0,hasMore=false)=>({[field]:rows,nextCursor,hasMore})
const reply={text:'真实回答',matches:[],historySync:'synced'}
function client(overrides={}) {return {invalidations:async()=>page('invalidations'),deletions:async()=>page('deletions'),snapshot:async()=>page('events'),ask:async()=>reply,...overrides}}
const turn={sessionId:'s',turnId:'t',query:'照片'}
await test('关闭历史同步保留回答及明确未同步标记',async()=>{
 const api=createCloudClient({baseUrl:'https://example.invalid',getUserToken:async()=> 'synthetic-token',fetch:async()=>new Response(JSON.stringify({answer:'真实回答',matches:[],sessionId:'s',turnId:'t',persisted:false}))})
 const r=await api.ask('照片',turn);assert.equal(r.text,'真实回答');assert.equal(r.historySync,'not_synced')
})
await test('通知URL与游标契约一致、未知通知不提交',async()=>{
 let url;let value={invalidations:[{kind:'paths_redacted',createdAt:'now'}],nextCursor:9,hasMore:false}
 const api=createCloudClient({baseUrl:'https://example.invalid',getUserToken:async()=> 'synthetic-token',fetch:async(u)=>{url=u;return new Response(JSON.stringify(value))}})
 assert.equal((await api.invalidations(2)).nextCursor,9);assert.equal(new URL(url).searchParams.get('after_id'),'2')
 value.invalidations[0].kind='unknown';await assert.rejects(()=>api.invalidations(),{code:'CLOUD_CONTRACT_MISMATCH'})
})
await test('任务按deviceId/jobId折叠、设备sequence不当游标',async()=>{
 const store=new Store();const scope=createOwnerSync('a',store,()=>client({snapshot:async()=>page('events',[job('d1',9),job('d2',2),job('d1',3)],77)}));await scope.sync();const state=await scope.view();assert.equal(state.tasks.size,2);assert.equal(state.cursors.tasks,77);assert.equal(state.tasks.get('["d1","j"]').sequence,9);assert.equal(state.cursors.invalidations,0)
})
await test('快照写入后的失效通知清理路径并原子提交独立游标',async()=>{
 const store=new Store();let reads=0;const scope=createOwnerSync('a',store,()=>client({invalidations:async(after)=>++reads===1?page('invalidations'):page('invalidations',[{kind:'paths_redacted',createdAt:'now'}],10),snapshot:async()=>page('events',[job()],5)}));await scope.sync();const state=await scope.view();assert.equal(reads,2);assert.equal(state.cursors.invalidations,10);assert.equal(state.cursors.tasks,5);for(const j of state.tasks.values()){assert.equal(j.payload.source,undefined);assert.equal(j.payload.paths,undefined)}
})
await test('清理事务失败不推进通知游标、不暴露缓存',async()=>{
 const store=new Store();store.fail=s=>s.cursors.invalidations===10;const scope=createOwnerSync('a',store,()=>client({invalidations:async()=>page('invalidations',[{kind:'paths_redacted',createdAt:'now'}],10)}));await assert.rejects(()=>scope.sync(),/simulated atomic rollback/);assert.equal((await store.read('a')).cursors.invalidations,0);await assert.rejects(()=>scope.view(),{code:'SYNC_NOT_READY'})
})
await test('已脱敏状态阻止后续旧快照重新填回路径',async()=>{
 const store=new Store();let notified=false;const scope=createOwnerSync('a',store,()=>client({invalidations:async(after)=>{if(notified)return page('invalidations',[],after);notified=true;return page('invalidations',[{kind:'paths_redacted',createdAt:'now'}],10)},snapshot:async(after)=>page('events',[job('d',after+1)],after+1)}));await scope.sync();await scope.sync();assert.equal((await scope.view()).tasks.get('["d","j"]').payload.source,undefined)
})
await test('删除通知同时清缓存、排队与独立删除游标',async()=>{
 const store=new Store();let deleted=false;const scope=createOwnerSync('a',store,()=>client({deletions:async(after)=>deleted?page('deletions',[{sessionId:'s',deletedAt:'now'}],13):page('deletions',[],after)}));await scope.enqueue(turn);await store.transaction('a',s=>s.turns.set('t',{sessionId:'s',reply}));deleted=true;assert.equal(await scope.flushOne(),null);const s=await store.read('a');assert.equal(s.pending.size,0);assert.equal(s.turns.size,0);assert.equal(s.cursors.deletions,13);assert.equal(s.cursors.tasks,0);await assert.rejects(()=>scope.enqueue(turn),{code:'SESSION_DELETED'})
})
await test('410 SESSION_DELETED禁止同session离线重放并保留错误',async()=>{
 const store=new Store();let calls=0;const scope=createOwnerSync('a',store,()=>client({ask:async()=>{calls++;throw new CloudError('SESSION_DELETED','已删除',410)}}));await scope.enqueue(turn);await assert.rejects(()=>scope.flushOne(),{code:'SESSION_DELETED',status:410});await assert.rejects(()=>scope.enqueue({...turn,turnId:'new'}),{code:'SESSION_DELETED'});assert.equal(await scope.flushOne(),null);assert.equal(calls,1)
})
await test('网络错误不吞，重试保留原轮次而不生成新ID',async()=>{
 const store=new Store();let sent=[];const scope=createOwnerSync('a',store,()=>client({ask:async(q,ids)=>{sent.push(ids);throw new CloudError('CLOUD_TIMEOUT','超时')}}));await scope.enqueue(turn);await assert.rejects(()=>scope.flushOne(),{code:'CLOUD_TIMEOUT'});await assert.rejects(()=>scope.flushOne(),{code:'CLOUD_TIMEOUT'});assert.deepEqual(sent,[{sessionId:'s',turnId:'t'},{sessionId:'s',turnId:'t'}]);assert.equal((await store.read('a')).pending.size,1)
})
await test('未同步回答返回但不写入历史',async()=>{
 const store=new Store();const scope=createOwnerSync('a',store,()=>client({ask:async()=>({...reply,historySync:'not_synced'})}));await scope.enqueue(turn);assert.equal((await scope.flushOne()).text,'真实回答');const s=await store.read('a');assert.equal(s.pending.size,0);assert.equal(s.turns.size,0)
})
await test('删除与回答竞争，后置通知阻止历史复活',async()=>{
 const store=new Store();let calls=0;const scope=createOwnerSync('a',store,()=>client({deletions:async()=>++calls===1?page('deletions'):page('deletions',[{sessionId:'s',deletedAt:'now'}],4)}));await scope.enqueue(turn);await assert.rejects(()=>scope.flushOne(),{code:'SESSION_DELETED'});assert.equal((await store.read('a')).turns.size,0)
})
await test('账号隔离三个游标、缓存和队列',async()=>{
 const store=new Store();const a=createOwnerSync('a',store,()=>client({snapshot:async()=>page('events',[job()],6)}));await a.sync();await a.enqueue(turn);const b=createOwnerSync('b',store,()=>client());await b.sync();assert.equal((await b.view()).tasks.size,0);assert.deepEqual((await b.view()).cursors,{tasks:0,deletions:0,invalidations:0});assert.equal(await b.flushOne(),null);assert.equal((await store.read('a')).pending.size,1)
})
await test('退出换号中止旧请求、旧结果不落缓存',async()=>{
 const store=new Store();let finish,started;const began=new Promise(r=>started=r);let signal;const scope=createOwnerSync('a',store,s=>{signal=s;return client({snapshot:()=>new Promise(r=>{finish=r;started()})})});const running=scope.sync();await began;scope.stop();assert.equal(signal.aborted,true);finish(page('events',[job()],6));await assert.rejects(()=>running,{code:'OWNER_SESSION_STOPPED'});assert.equal((await store.read('a')).tasks.size,0);await assert.rejects(()=>scope.enqueue(turn),{code:'OWNER_SESSION_STOPPED'})
})
await test('job/media分类保留且不伪造文件路径',async()=>{
 const r=parseCloudReply({answer:'任务',matches:[{kind:'job',deviceId:'d',jobId:'j',name:'n',state:'success',sequence:1,occurredAt:'now',source:'synced_job_snapshot'}]});assert.equal(r.matches[0].kind,'job');assert.equal('path' in r.matches[0],false);assert.throws(()=>parseCloudReply({answer:'x',matches:[{kind:'media'}]}),{code:'CLOUD_CONTRACT_MISMATCH'})
})
await test('快照后通知网络失败保持不可见且不误报成功',async()=>{
 const store=new Store();let reads=0;const scope=createOwnerSync('a',store,()=>client({snapshot:async()=>page('events',[job()],2),invalidations:async()=>{if(++reads>1)throw new CloudError('CLOUD_UNREACHABLE','离线');return page('invalidations')}}));await assert.rejects(()=>scope.sync(),{code:'CLOUD_UNREACHABLE'});await assert.rejects(()=>scope.view(),{code:'SYNC_NOT_READY'})
})
await test('真实HTTP客户端忽略中止的迟到响应仍不返回旧账号结果',async()=>{
 let finish,started;const began=new Promise(r=>started=r), controller=new AbortController()
 const api=createCloudClient({baseUrl:'https://example.invalid',signal:controller.signal,getUserToken:async()=> 'synthetic-token',fetch:()=>new Promise(r=>{finish=r;started()})})
 const request=api.ask('照片');await began;controller.abort();finish(new Response(JSON.stringify({answer:'旧回答',matches:[]})))
 await assert.rejects(()=>request,{code:'OWNER_SESSION_STOPPED'})
})
console.log(`${passed} R4 checks passed; pure client + synthetic transport, no real identity/cloud acceptance.`)
