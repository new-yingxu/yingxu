#!/usr/bin/env python3
"""Explicitly selected source; no writes to source, no private media in evidence."""
import argparse,collections,hashlib,json,pathlib,subprocess,tempfile,time
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--evidence',required=True);args=p.parse_args()
source=pathlib.Path(args.source).resolve(strict=True);binary=pathlib.Path(__file__).resolve().parents[1]/'target/release/yingxu-core'
photos=sorted(x for x in source.rglob('*') if x.is_file() and not x.is_symlink() and not any(v.startswith('.') for v in x.relative_to(source).parts) and x.suffix.lower() in {'.jpg','.jpeg','.png'})
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while chunk:=f.read(1024*1024):h.update(chunk)
 return h.hexdigest()
before={x:(x.stat().st_size,x.stat().st_mtime_ns,digest(x)) for x in photos}
start=time.monotonic();result={'sourceType':'user-authorized removable FAT32 card','originalCount':len(photos),'formats':dict(collections.Counter(x.suffix.lower() for x in photos)),'originalBytes':sum(v[0] for v in before.values()),'privatePathsIncluded':False,'networkUsed':False}
with tempfile.TemporaryDirectory(prefix='yingxu-readonly-card-') as tmp:
 root=pathlib.Path(tmp).resolve();dest=root/'copy';dest.mkdir()
 process=subprocess.Popen([str(binary),'--data-dir',str(root/'db'),'rpc'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 sequence=0
 def rpc(method,params):
  global sequence
  sequence+=1;process.stdin.write(json.dumps({'requestId':str(sequence),'version':1,'method':method,'params':params})+'\n');process.stdin.flush();reply=json.loads(process.stdout.readline())
  if not reply['ok']:raise RuntimeError(reply['error']['code'])
  return reply.get('data')
 try:
  result['index']=rpc('library.index',{'source':str(source)})
  rows=rpc('library.search',{'query':'','kind':'photo','quality':'all'})
  decode_reasons=collections.Counter();decoded=collections.Counter();quality=collections.Counter();hints=collections.Counter();timestamps=collections.Counter();errors=collections.Counter();thumbnail_checked=0
  for row in rows:
   try:
    response=rpc('library.analyzePhoto',{'mediaId':row['id']});meta=response['metadata'];decoded[meta['decodeStatus']]+=1;
    if meta.get('decodeReason'):decode_reasons[meta['decodeReason']]+=1
    quality[response['qualityState']]+=1;timestamps[meta['timestampSource']]+=1
    hints.update(meta.get('quality',{}).get('candidates',[]))
    if response['thumbnailAvailable'] and thumbnail_checked<3:
     thumb=rpc('library.thumbnail',{'mediaId':row['id']});assert thumb['maxEdge']==512 and thumb['exifIncluded'] is False and len(thumb['bytes'])<1024*1024;thumbnail_checked+=1
   except RuntimeError as e:errors[str(e)]+=1
  result.update(decoded=dict(decoded),decodeReasons=dict(decode_reasons),qualityCandidates=dict(quality),signalCounts=dict(hints),timestampSources=dict(timestamps),analysisErrors=dict(errors),thumbnailReadChecks=thumbnail_checked,qualityAccuracy='not_established_no_ground_truth_labels')
  selected=[]
  for ext in {x.suffix.lower() for x in photos}:
   selected.append(next(x for x in photos if x.suffix.lower()==ext))
  plan=rpc('backup.preview',{'source':str(source),'destinations':[str(dest)],'name':'Read-only card verification','verify':True,'selected':[str(x.relative_to(source)) for x in selected]})
  job=rpc('jobs.start',{'planId':plan['id'],'confirmed':True,'idempotencyKey':'card-check'})
  completed=rpc('worker.runNext',{})
  assert completed['state']=='success'
  matches=all(digest(source/item['relative'])==digest(pathlib.Path(item['output'])) for item in completed['items'])
  result['sampleBackup']={'files':len(selected),'state':completed['state'],'verified':completed['targets'][0]['verified'],'independentSha256Match':matches}
 finally:
  process.stdin.close();process.wait(timeout=10)
result['sourceContentUnchanged']=all((x.stat().st_size,x.stat().st_mtime_ns,digest(x))==values for x,values in before.items())
result['elapsedSeconds']=round(time.monotonic()-start,2)
assert result['sourceContentUnchanged']
pathlib.Path(args.evidence).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
