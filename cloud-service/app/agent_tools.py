"""Owner-bound adapters. File writes remain local-confirmation proposals."""
from pydantic import BaseModel,ConfigDict,Field


class SearchArguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=500)


class JobArguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
    jobId:str=Field(min_length=1,max_length=500)
    deviceId:str|None=Field(default=None,min_length=1,max_length=500)


class ToolNotAllowed(ValueError):
    pass


class ReadOnlyToolRegistry:
    def __init__(self,store,principal):
        self.store=store
        self.principal=principal

    def invoke(self,name,arguments):
        if name=='get_backup_job':
            args=JobArguments.model_validate(arguments)
            result=self.search(args.jobId)
            jobs=[row for row in result['matches'] if row['jobId']==args.jobId and (args.deviceId is None or row['deviceId']==args.deviceId)]
            return {'jobs':jobs,'found':bool(jobs),'searchWindow':result['searchWindow'],
                    'reason':None if jobs else 'NOT_IN_SEARCH_WINDOW','executionAuthorized':False}
        if name!='search_backup_jobs':
            raise ToolNotAllowed('tool is not enabled for cloud execution')
        args=SearchArguments.model_validate(arguments)
        if not args.query.strip():raise ValueError('empty query')
        return self.search(args.query)

    def search(self,query):
        rows = self.store.events(self.principal.owner_id, limit=200, newest=True)
        term = query.casefold()
        latest = {}
        for row in rows:
            job_id = row.body_json.get("payload", {}).get("jobId")
            if not job_id:
                continue
            key = (row.device_id, job_id)
            if key not in latest or row.sequence > latest[key].sequence:
                latest[key] = row
        matches = []
        for row in latest.values():
            job_payload = row.body_json.get("payload", {})
            haystack = f"{job_payload.get('name', '')} {job_payload.get('jobId', '')} {job_payload.get('state', '')}".casefold()
            if term in haystack:
                matches.append({
                    "kind": "job",
                    "deviceId": row.device_id,
                    "sequence": row.sequence,
                    "jobId": job_payload.get("jobId"),
                    "name": job_payload.get("name"),
                    "state": job_payload.get("state"),
                    "occurredAt": row.body_json.get("occurredAt"),
                    "source": "synced_job_snapshot",
                })
        response = {"answer": f"最近同步的任务摘要中找到 {len(matches)} 条匹配。" if matches else "最近同步的任务摘要中没有匹配。", "matches": matches[:50], "model": None, "dataSource": "owner_scoped_sync_events", "searchWindow": {"eventLimit": 200, "complete": False}}
        return response


def tool_catalog():
    return [
        {'name':'search_backup_jobs','permission':'read','executableInCloud':True,'scope':'owner_scoped_recent_200_job_events'},
        {'name':'get_backup_job','permission':'read','executableInCloud':True,'scope':'owner_scoped_recent_200_job_events'},
        {'name':'rename_item','permission':'proposal_only','executableInCloud':False,'requiresLocalConfirmation':True},
        {'name':'move_item','permission':'proposal_only','executableInCloud':False,'requiresLocalConfirmation':True},
        {'name':'upload_baidu','permission':'proposal_only','executableInCloud':False,'requiresLocalConfirmation':True},
    ]
