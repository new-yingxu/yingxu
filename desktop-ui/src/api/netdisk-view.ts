import type { NetdiskTask } from './contracts'
export function uploadState(task: NetdiskTask) {
  if(task.state==='completed'&&task.verifiedRemote)return '已上传并通过远端校验'
  if(task.state==='completed')return '远端校验未确认'
  return ({queued:'排队待上传',uploading:'上传中',running:'上传中',failed:'上传失败，可检查后续传',cancelled:'已停止',paused:'已暂停'} as Record<string,string>)[task.state]??task.state
}
export function uploadChunks(task: NetdiskTask) { return `${task.chunksDone}/${task.chunksTotal} 分片` }
