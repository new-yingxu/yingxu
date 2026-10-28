"""HTTP-to-real-LangGraph-to-owner-tool contracts, synthetic provider only."""
import json
from unittest.mock import patch
import unittest
import test_graph_checkpoint_http as fixtures
from app.models import utcnow

class ModelToolHTTP(unittest.TestCase):
    setUp=fixtures.GraphCheckpointHTTP.setUp
    tearDown=fixtures.GraphCheckpointHTTP.tearDown
    post=fixtures.GraphCheckpointHTTP.post
    def test_tool_result_consumed_then_duplicate_and_resume_do_not_redispatch(self):
        self.assertEqual(self.client.put('/v1/account/sync-preferences',json={'syncRecords':True,'syncAgentSessions':True,'syncThumbnails':False,'syncPaths':True},headers=self.headers).status_code,200)
        event={'schemaVersion':1,'eventId':'tool-event','deviceId':'local-dev-device','sequence':1,'eventType':'job.snapshot.v1','occurredAt':utcnow().isoformat(),'payload':{'jobId':'fixture-job','name':'Fixture','state':'running','phase':'copy','files':1,'bytes':2,'progress':10,'targets':[],'paths':['PRIVATE_PATH']}}
        self.assertEqual(self.client.post('/v1/sync/events',json={'events':[event]},headers=self.headers).status_code,200)
        seen=[]
        def wire(credential,messages):
            seen.append(messages)
            if len(seen)==1:
                return {'choices':[{'message':{'tool_calls':[{'id':'read-job','type':'function','function':{'name':'get_backup_job','arguments':json.dumps({'jobId':'fixture-job'})}}]}}],'usage':{'total_tokens':3}}
            result=json.loads(messages[-1]['content'])['syncedJobSummaries']
            self.assertEqual(result[0]['jobId'],'fixture-job')
            self.assertEqual(result[0]['state'],'running')
            self.assertEqual(set(result[0]),{'jobId','name','state','occurredAt'})
            self.assertEqual(messages[-1]['tool_call_id'],'read-job')
            self.assertNotIn('PRIVATE_PATH',str(messages))
            self.assertNotIn('synthetic-never-serialize-key',str(messages))
            return {'choices':[{'message':{'content':'任务运行中；最近同步窗口可能不完整。'}}],'usage':{'total_tokens':4}}
        with patch('app.model_provider._tool_provider_request',side_effect=wire) as provider:
            first=self.post(useModel=True)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()['usage']['total_tokens'],7)
            self.assertFalse(first.json()['executionAuthorized'])
            self.assertTrue(self.post(useModel=True).json()['duplicate'])
            self.assertTrue(self.post(useModel=True,resume=True).json()['duplicate'])
            self.assertEqual(provider.call_count,2)
    def test_write_tool_refusal_becomes_uncertain_without_retry(self):
        invalid={'choices':[{'message':{'tool_calls':[{'id':'write','type':'function','function':{'name':'rename_item','arguments':'{}'}}]}}]}
        with patch('app.model_provider._tool_provider_request',return_value=invalid) as provider:
            first=self.post(useModel=True)
            self.assertEqual(first.status_code,502,first.text)
            self.assertEqual(first.json()['detail']['code'],'MODEL_OUTCOME_UNCERTAIN')
            resumed=self.post(useModel=True,resume=True)
            self.assertEqual(resumed.status_code,409,resumed.text)
            self.assertEqual(resumed.json()['detail']['code'],'MODEL_REQUEST_UNCERTAIN')
            self.assertEqual(provider.call_count,1)
