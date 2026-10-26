"""Offline HTTP contracts for persistent LangGraph turns and explicit resume."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from sqlalchemy import select
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.auth import Principal, require_principal
from app.graph_checkpoints import GraphCheckpoint
from app.models import AgentModelRequest, SyncPreferences
from app.store import SQLStore, StoreUnavailable
from app.model_provider import ModelUnavailable


class GraphCheckpointHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'APP_ENV':'development','AUTH_MODE':'dev','DEV_AUTH_TOKEN':'checkpoint-fixture',
            'DATABASE_URL':'sqlite:///'+self.temp.name+'/http.sqlite',
            'MODEL_OWNER_CREDENTIALS':json.dumps({'local-dev-owner':{'apiKey':'synthetic-never-serialize-key'}})})
        self.env.start()
        self.client=TestClient(app,raise_server_exceptions=False)
        self.client.__enter__()
        self.headers={'Authorization':'Bearer checkpoint-fixture'}
        self.assertEqual(self.client.post('/v1/devices/register',json={'displayName':'Fixture'},headers=self.headers).status_code,200)
        self.payload={'query':'Fixture','sessionId':'session','turnId':'turn'}
        self.generated={'answer':'Synthetic answer','model':'qwen-plus','provider':'dashscope','usage':{'total_tokens':1},'executionAuthorized':False}
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop();self.temp.cleanup()
    def post(self,**changes):
        return self.client.post('/v1/agent/turns',json={**self.payload,**changes},headers=self.headers)
    def optout(self):
        return self.client.put('/v1/account/sync-preferences',json={'syncRecords':True,'syncAgentSessions':False,'syncThumbnails':False,'syncPaths':False},headers=self.headers)
    def checkpoints(self):
        with app.state.session_factory() as db:return db.scalars(select(GraphCheckpoint)).all()

    def test_failed_readonly_turn_resumes_after_application_restart(self):
        with patch.object(SQLStore,'save_turn',side_effect=StoreUnavailable('planned persistence failure')):
            first=self.post()
        self.assertEqual(first.status_code,503,first.text)
        self.assertTrue(self.checkpoints())
        # Destroy the app lifespan/engine and reconstruct them against the same file.
        self.client.__exit__(None,None,None)
        self.client=TestClient(app,raise_server_exceptions=False);self.client.__enter__()
        with patch('app.main.ReadOnlyToolRegistry.invoke',side_effect=AssertionError('retrieval must not rerun')):
            resumed=self.post(resume=True)
        self.assertEqual(resumed.status_code,200,resumed.text)
        self.assertTrue(resumed.json()['persisted'])
        self.assertTrue(self.post(resume=True).json()['duplicate'])
        self.assertTrue(self.post().json()['duplicate'])
        self.assertEqual(self.post(query='Changed').status_code,409)
        self.assertEqual(self.post(query='Changed',resume=True).json()['detail']['code'],'TURN_ID_CONFLICT')

    def test_model_completed_resume_checks_ledger_without_second_provider_call(self):
        request={'useModel':True}
        with patch('app.main.generate_answer',return_value=self.generated) as provider:
            first=self.post(**request)
            self.assertEqual(first.status_code,200,first.text)
            original=SQLStore.reserve_model
            with patch.object(SQLStore,'reserve_model',autospec=True,side_effect=original) as reserve:
                resumed=self.post(**request,resume=True)
            self.assertEqual(resumed.status_code,200,resumed.text)
            self.assertTrue(resumed.json()['duplicate']);reserve.assert_called_once()
            self.assertTrue(self.post(**request).json()['duplicate'])
            self.assertEqual(provider.call_count,1)
        raw=json.dumps([row.payload for row in self.checkpoints()])
        self.assertNotIn('synthetic-never-serialize-key',raw)
        self.assertNotIn('MODEL_OWNER_CREDENTIALS',raw)
        self.assertEqual(self.post(resume=True).json()['detail']['code'],'CHECKPOINT_NOT_FOUND')

    def test_uncertain_and_pending_model_outcomes_never_redispatch(self):
        for turn,failure,code,status in [('uncertain',ModelUnavailable('fixture'),'MODEL_REQUEST_UNCERTAIN',502),
                                         ('pending',RuntimeError('simulated process failure'),'MODEL_REQUEST_PENDING',500)]:
            with self.subTest(turn=turn),patch('app.main.generate_answer',side_effect=failure) as provider:
                first=self.post(turnId=turn,useModel=True)
                self.assertEqual(first.status_code,status,first.text)
                resumed=self.post(turnId=turn,useModel=True,resume=True)
                self.assertEqual(resumed.status_code,409,resumed.text)
                self.assertEqual(resumed.json()['detail']['code'],code)
                self.assertFalse(resumed.json()['detail']['automaticRetryAllowed'])
                self.assertEqual(provider.call_count,1)
                self.assertEqual(self.post(turnId=turn,useModel=True).status_code,409)
                self.assertEqual(provider.call_count,1)

    def test_optout_deletion_revocation_and_resume_validation(self):
        self.assertEqual(self.post(resume=True).json()['detail']['code'],'CHECKPOINT_NOT_FOUND')
        missing=self.client.post('/v1/agent/turns',json={'query':'Fixture','resume':True},headers=self.headers)
        self.assertEqual(missing.status_code,422)
        self.assertEqual(self.post(thread_id='client-injection').status_code,422)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.optout().status_code,200)
        before=len(self.checkpoints())
        self.assertEqual(self.post(resume=True).json()['detail']['code'],'CHECKPOINT_SYNC_DISABLED')
        private=self.post(turnId='private')
        self.assertEqual(private.status_code,200,private.text);self.assertFalse(private.json()['persisted'])
        self.assertEqual(len(self.checkpoints()),before)
        deleted=self.client.request('DELETE','/v1/agent/sessions/session',json={'confirmed':True},headers=self.headers)
        self.assertEqual(deleted.status_code,200);self.assertEqual(self.checkpoints(),[])
        # Re-enable history: the tombstone still rejects both new runs and resume.
        self.client.put('/v1/account/sync-preferences',json={'syncRecords':True,'syncAgentSessions':True,'syncThumbnails':False,'syncPaths':False},headers=self.headers)
        self.assertEqual(self.post(resume=True).status_code,410)
        self.assertEqual(self.post().status_code,410)
        app.dependency_overrides[require_principal]=lambda:Principal(owner_id='local-dev-owner',device_id='other-device',origin='desktop')
        try:
            self.client.post('/v1/devices/register',json={'displayName':'Other'},headers=self.headers)
            revoked=self.client.post('/v1/devices/local-dev-device/revoke',headers=self.headers)
            self.assertEqual(revoked.status_code,200)
        finally:
            app.dependency_overrides.pop(require_principal,None)
        self.assertEqual(self.post(resume=True).status_code,403)

    def test_single_connection_pool_releases_request_and_node_sessions(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        app.state.engine.dispose()
        engine=create_engine('sqlite:///'+self.temp.name+'/http.sqlite',pool_size=1,max_overflow=0,pool_timeout=0.2,
                             connect_args={'check_same_thread':False})
        app.state.engine=engine
        app.state.session_factory=sessionmaker(bind=engine,expire_on_commit=False)
        response=self.post()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.post(resume=True).status_code,200)
        with patch('app.main.generate_answer',return_value=self.generated) as provider:
            modeled=self.post(turnId='model-pool',useModel=True)
            self.assertEqual(modeled.status_code,200,modeled.text)
            replay=self.post(turnId='model-pool',useModel=True,resume=True)
            self.assertEqual(replay.status_code,200,replay.text)
            self.assertEqual(provider.call_count,1)
        self.assertEqual(engine.pool.checkedout(),0)

    def test_midcall_optout_completes_answer_without_checkpoint_resurrection(self):
        def provider(*args,**kwargs):
            with app.state.session_factory.begin() as db:
                db.add(SyncPreferences(owner_id='local-dev-owner',sync_agent_sessions=False))
            return self.generated
        with patch('app.main.generate_answer',side_effect=provider) as generate:
            result=self.post(useModel=True)
            self.assertEqual(result.status_code,200,result.text)
            self.assertFalse(result.json()['persisted'])
            count=len(self.checkpoints())
            self.assertEqual(self.post(useModel=True,resume=True).status_code,409)
            repeat=self.post(useModel=True)
            self.assertEqual(repeat.json()['detail']['code'],'MODEL_REQUEST_COMPLETED_UNRETAINED')
            self.assertEqual(generate.call_count,1)
        self.assertEqual(len(self.checkpoints()),count)
        with app.state.session_factory() as db:
            row=db.scalar(select(AgentModelRequest))
            self.assertEqual(row.state,'completed_unretained')
        # The actual model output was never written into a graph checkpoint.
        self.assertNotIn('Synthetic answer',json.dumps([row.payload for row in self.checkpoints()]))


if __name__=='__main__':unittest.main()
