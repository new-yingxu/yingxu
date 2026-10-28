"""Offline durable local-step reservations; no model/provider requests."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4
import unittest
from unittest.mock import MagicMock,patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Base
from app.models import Device,SyncPreferences,DeletedAgentSession
from app.store import SQLStore,StoreConflict,DeviceUnavailable,SessionDeleted
from app.local_step_store import LocalStep,LocalStepStore


class LocalSteps(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/steps.sqlite')
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.p=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.helper=LocalStepStore(self.store,self.p)
        for owner,device,origin in [('owner','desktop','desktop'),('owner','desktop2','desktop'),('other','desktop','desktop'),('owner','mobile','mobile')]:
            self.db.add(Device(owner_id=owner,device_id=device,origin=origin))
        self.db.commit();self.root='a'*64;self.input='b'*64
    def tearDown(self):self.db.close();self.engine.dispose();self.temp.cleanup()
    def reserve(self,step=0,**changes):
        return self.helper.reserve('session','turn',step,changes.get('root_hash',self.root),changes.get('input_hash',self.input),changes.get('parent_nonce'))
    def finish(self,attempt,step=0,state='completed',response=None,nonce=None):
        return self.helper.finish('session','turn',step,attempt,state,response,nonce)
    def row(self,step=0):return self.db.get(LocalStep,('owner','session','turn',step),populate_existing=True)

    def test_once_reserve_finish_and_new_store_cached_replay(self):
        first=self.reserve();self.assertEqual(first['state'],'reserved')
        created=self.row().created_at
        self.assertEqual(self.reserve(),{'state':'pending','retained':False})
        self.assertEqual(self.row().created_at,created)
        body={'answer':'local-only answer','usage':{'total_tokens':3}}
        self.assertEqual(self.finish(first['attemptId'],response=body),{'state':'completed','retained':True})
        with self.sessions() as db:
            replay=LocalStepStore(SQLStore(db),self.p).reserve('session','turn',0,self.root,self.input)
        self.assertEqual(replay,{'state':'replayed','response':body})
        replay['response']['answer']='mutated'
        self.assertEqual(self.reserve()['response'],body)
        with self.assertRaises(StoreConflict):self.finish(first['attemptId'],response=body)

    def test_hash_device_owner_cas_and_attempt_isolation(self):
        reserved=self.reserve()
        for hashes in ({'root_hash':'c'*64},{'input_hash':'d'*64}):
            with self.assertRaises(StoreConflict):self.reserve(**hashes)
        other_device=LocalStepStore(self.store,SimpleNamespace(owner_id='owner',device_id='desktop2',origin='desktop'))
        with self.assertRaises(StoreConflict):other_device.reserve('session','turn',0,self.root,self.input)
        with self.assertRaises(StoreConflict):other_device.finish('session','turn',0,reserved['attemptId'],'completed',{})
        other_owner=LocalStepStore(self.store,SimpleNamespace(owner_id='other',device_id='desktop',origin='desktop'))
        self.assertEqual(other_owner.reserve('session','turn',0,self.root,self.input)['state'],'reserved')
        with self.assertRaises(StoreConflict):self.finish(str(uuid4()),response={})
        self.assertEqual(self.reserve(),{'state':'pending','retained':False})

    def test_parent_nonce_three_step_chain_and_unknown_parent_rejected(self):
        missing=str(uuid4())
        with self.assertRaises(StoreConflict):self.reserve(1,parent_nonce=missing)
        zero=self.reserve();nonce0=str(uuid4())
        self.finish(zero['attemptId'],state='awaiting_tools',response={'toolCalls':[{'name':'read_metadata'}]},nonce=nonce0)
        with self.assertRaises(StoreConflict):self.reserve(1,parent_nonce=str(uuid4()))
        with self.assertRaises(StoreConflict):self.reserve(1,parent_nonce=nonce0,root_hash='c'*64)
        one=self.reserve(1,parent_nonce=nonce0,input_hash='c'*64);nonce1=str(uuid4())
        self.finish(one['attemptId'],1,'awaiting_tools',{'toolCalls':[{'name':'search_files'}]},nonce1)
        with self.assertRaises(StoreConflict):self.reserve(2,parent_nonce=nonce0)
        two=self.reserve(2,parent_nonce=nonce1,input_hash='d'*64)
        self.finish(two['attemptId'],2,'completed',{'answer':'complete'})
        self.assertEqual(self.reserve(2,parent_nonce=nonce1,input_hash='d'*64)['state'],'replayed')
        with self.assertRaises(ValueError):self.reserve(3,parent_nonce=str(uuid4()))
        self.assertEqual(len(self.db.scalars(select(LocalStep)).all()),3)

    def test_disabled_cache_keeps_only_hash_status_nonce_and_does_not_redispatch(self):
        reserved=self.reserve();nonce=str(uuid4())
        with self.sessions.begin() as db:db.add(SyncPreferences(owner_id='owner',sync_agent_sessions=False))
        self.assertEqual(self.finish(reserved['attemptId'],state='awaiting_tools',response={'answer':'not retained'},nonce=nonce),{'state':'awaiting_tools','retained':False})
        row=self.row();self.assertIsNone(row.response_json);self.assertEqual(row.output_nonce,nonce)
        self.assertEqual(self.reserve(),{'state':'awaiting_tools','retained':False})
        with self.sessions.begin() as db:db.get(SyncPreferences,'owner').sync_agent_sessions=True
        self.assertEqual(self.reserve(),{'state':'awaiting_tools','retained':False})
        next_step=self.reserve(1,parent_nonce=nonce)
        self.assertEqual(next_step['state'],'reserved')

    def test_existing_cache_is_never_recovered_while_preference_disabled(self):
        reserved=self.reserve();self.finish(reserved['attemptId'],response={'answer':'cached'})
        with self.sessions.begin() as db:db.add(SyncPreferences(owner_id='owner',sync_agent_sessions=False))
        self.assertEqual(self.reserve(),{'state':'completed','retained':False})

    def test_session_deletion_revocation_mobile_and_late_finish_rejected(self):
        reserved=self.reserve()
        with self.sessions.begin() as db:db.add(DeletedAgentSession(owner_id='owner',session_id='session'))
        with self.assertRaises(SessionDeleted):self.reserve()
        with self.assertRaises(SessionDeleted):self.finish(reserved['attemptId'],response={})
        with self.sessions.begin() as db:
            db.query(DeletedAgentSession).delete()
            db.scalar(select(Device).where(Device.owner_id=='owner',Device.device_id=='desktop')).revoked=True
        with self.assertRaises(DeviceUnavailable):self.reserve()
        with self.assertRaises(DeviceUnavailable):self.finish(reserved['attemptId'],response={})
        self.assertEqual(self.row().state,'pending')
        with self.assertRaises(DeviceUnavailable):LocalStepStore(self.store,SimpleNamespace(owner_id='owner',device_id='mobile',origin='mobile'))

    def test_uncertain_no_cache_no_retry_no_late_finish(self):
        reserved=self.reserve()
        self.assertEqual(self.finish(reserved['attemptId'],state='uncertain',response={'apiKey':'must-not-persist'}),{'state':'uncertain','retained':False})
        self.assertEqual(self.reserve(),{'state':'uncertain','retained':False})
        self.assertIsNone(self.row().response_json)
        with self.assertRaises(StoreConflict):self.finish(reserved['attemptId'],response={'answer':'late'})

    def test_strict_validation_secrets_and_rollback_preserve_pending(self):
        for step in (-1,3,True):
            with self.assertRaises(ValueError):self.reserve(step)
        for changes in ({'root_hash':'A'*64},{'input_hash':'not a hash'},{'parent_nonce':str(uuid4())}):
            with self.assertRaises(ValueError):self.reserve(**changes)
        reserved=self.reserve()
        for bad in ({'apiKey':'secret'},{'nested':[{'Authorization':'secret'}]},{'nested':({'accessToken':'secret'},)},{'notFinite':float('nan')}):
            with self.assertRaises(ValueError):self.finish(reserved['attemptId'],response=bad)
        with self.assertRaises(ValueError):self.finish(reserved['attemptId'],state='awaiting_tools',response={})
        with patch.object(self.db,'commit',side_effect=RuntimeError('commit failed')):
            with self.assertRaises(RuntimeError):self.finish(reserved['attemptId'],response={'answer':'must rollback'})
        self.assertEqual(self.row().state,'pending');self.assertIsNone(self.row().response_json)
        self.assertNotIn('secret',str(self.row().__dict__))

    def test_rpc_server_binding_and_payload_projection(self):
        backend=MagicMock();backend.request.return_value={'state':'reserved','attemptId':str(uuid4())}
        rpc=LocalStepStore(backend,self.p)
        rpc.reserve('session','turn',0,self.root,self.input)
        call=backend.request.call_args
        self.assertEqual(call.args,('rpc/yingxu_local_agent_step','POST'))
        self.assertEqual(call.kwargs['body'],{'p_owner':'owner','p_device':'desktop','p_origin':'desktop','p_operation':'reserve','p_session':'session','p_turn':'turn','p_step':0,
                                             'p_payload':{'root_hash':self.root,'input_hash':self.input,'parent_nonce':None}})
        backend.request.reset_mock()
        with self.assertRaises(ValueError):rpc.finish('session','turn',0,str(uuid4()),'completed',{'api_key':'secret'})
        backend.request.assert_not_called()


if __name__=='__main__':unittest.main()
