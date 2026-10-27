"""Only temporary SQLite fixtures; never accesses real account data/network."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Base
from app.models import (Device,SyncPreferences,SyncEvent,AgentTurn,AgentAction,DeletedAgentSession,AgentModelRequest,SyncInvalidation,utcnow)
from app.graph_checkpoints import GraphCheckpoint,GraphPendingWrite,PrincipalCheckpointSaver
from app.preset_store import PresetRevision,PresetStore,PresetSyncDisabled
from app.sync_snapshots import SyncSnapshotLease
from app.account_data import AccountDataStore,DataCleanupReceipt
from app.store import SQLStore,DeviceUnavailable,StoreConflict,SessionDeleted,SyncForbidden

TABLES=(Device,SyncPreferences,SyncEvent,AgentTurn,AgentAction,DeletedAgentSession,AgentModelRequest,SyncInvalidation,GraphCheckpoint,GraphPendingWrite,PresetRevision,DataCleanupReceipt,SyncSnapshotLease)


class AccountData(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/cleanup.sqlite')
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.principal=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.cleanup=AccountDataStore(self.store,self.principal)
        self.seed('owner');self.seed('other')
    def tearDown(self):self.db.close();self.engine.dispose();self.temp.cleanup()
    def seed(self,owner):
        db=self.db
        db.add_all([Device(owner_id=owner,device_id='desktop',origin='desktop',display_name='private device'),
                    Device(owner_id=owner,device_id='mobile',origin='mobile',display_name='private mobile'),
                    Device(owner_id=owner,device_id='old',origin='desktop',display_name='old private',revoked=True),
                    SyncPreferences(owner_id=owner,sync_records=True,sync_agent_sessions=True,sync_paths=True,sync_thumbnails=True),
                    SyncEvent(owner_id=owner,device_id='desktop',event_id='event',sequence=1,body_json={'text':'private event'},request_hash='a'*64),
                    AgentTurn(owner_id=owner,device_id='desktop',session_id='turn-session',turn_id='turn',query='private query',response_json={'text':'private response'}),
                    AgentAction(owner_id=owner,id='action',device_id='desktop',origin='desktop',tool='rename_item',proposal_json={'text':'private proposal'},expires_at=utcnow()+timedelta(days=1)),
                    DeletedAgentSession(owner_id=owner,session_id='already-deleted'),
                    GraphCheckpoint(owner_id=owner,session_id='graph-session',thread_id='thread',checkpoint_ns='',checkpoint_id='cp',payload={'text':'private graph'}),
                    GraphPendingWrite(owner_id=owner,session_id='write-session',thread_id='thread',checkpoint_ns='',checkpoint_id='cp',task_id='task',idx=0,payload={'text':'private write'}),
                    SyncSnapshotLease(snapshot_id=('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' if owner=='owner' else 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),owner_id=owner,device_id='desktop',origin='desktop',through_cursor=0,expires_at=utcnow()+timedelta(minutes=5)),
                    SyncInvalidation(owner_id=owner,kind='paths_redacted'),SyncInvalidation(owner_id=owner,kind='another_old'),
                    PresetRevision(owner_id=owner,preset_id='a',version=1,body_json={'name':'private old preset','pattern':'private pattern','isDefault':False}),
                    PresetRevision(owner_id=owner,preset_id='a',version=2,body_json={'name':'private preset','pattern':'private pattern','isDefault':True}),
                    PresetRevision(owner_id=owner,preset_id='b',version=1,body_json={'name':'private b','pattern':'private pattern','isDefault':False}),
                    PresetRevision(owner_id=owner,preset_id='deleted',version=1,body_json={},deleted=True)])
        for state in ('completed','pending','uncertain'):
            db.add(AgentModelRequest(owner_id=owner,session_id=state+'-session',turn_id=state,device_id='desktop',request_hash='b'*64,attempt_id=state+'-attempt',state=state))
        db.commit()
    def rows(self,model,owner='owner'):
        return self.db.scalars(select(model).where(model.owner_id==owner).execution_options(populate_existing=True)).all()
    def snapshot(self,owner):
        return {model.__tablename__:deepcopy(self.db.execute(select(*model.__table__.c).where(model.__table__.c.owner_id==owner)).all()) for model in TABLES}

    def test_atomic_content_erasure_tombstones_metadata_and_other_owner_isolation(self):
        other=self.snapshot('other')
        receipt=self.cleanup.clear('request',True)
        self.assertTrue(receipt['cleared']);self.assertEqual(receipt['devicesRevoked'],2)
        self.assertEqual(receipt['counts'],{'sessionTombstonesAdded':6,'syncEvents':1,'agentTurns':1,'agentActions':1,'graphPendingWrites':1,'graphCheckpoints':1,'modelRequestsRetained':3,'presetRevisionsCleared':4,'presetTombstonesAdded':2,'previousInvalidationsRemoved':2})
        for model in (SyncEvent,AgentTurn,AgentAction,GraphCheckpoint,GraphPendingWrite,SyncSnapshotLease):self.assertEqual(self.rows(model),[])
        for row in self.rows(Device):self.assertTrue(row.revoked);self.assertEqual(row.display_name,'')
        presets=self.rows(PresetRevision);self.assertEqual(len(presets),6)
        self.assertTrue(all(row.deleted and row.body_json=={} for row in presets))
        self.assertEqual({(r.preset_id,r.version) for r in presets if (r.preset_id,r.version) in {('a',3),('b',2)}},{('a',3),('b',2)})
        states={r.state for r in self.rows(AgentModelRequest)}
        self.assertEqual(states,{'completed_unretained','pending','uncertain'})
        self.assertTrue(all(r.request_hash=='b'*64 for r in self.rows(AgentModelRequest)))
        self.assertEqual(len(self.rows(DeletedAgentSession)),7)
        self.assertEqual([r.kind for r in self.rows(SyncInvalidation)],['all_sync_data_deleted'])
        prefs=self.rows(SyncPreferences)[0]
        self.assertFalse(any(getattr(prefs,f) for f in ('sync_records','sync_agent_sessions','sync_thumbnails','sync_paths')))
        self.assertEqual(self.snapshot('other'),other)
        self.assertNotIn('private',str(receipt));self.assertNotIn('private',str([r.result_json for r in self.rows(DataCleanupReceipt)]))
        self.assertEqual(len(self.rows(DataCleanupReceipt)),1)

    def test_exact_receipt_replay_after_revocation_and_cross_device_conflict(self):
        result=self.cleanup.clear('request',True)
        replay=self.cleanup.clear('request',True)
        self.assertEqual(result,replay)
        replay['counts']['syncEvents']=999
        self.assertEqual(self.cleanup.clear('request',True),result)
        wrong=AccountDataStore(self.store,SimpleNamespace(owner_id='owner',device_id='mobile',origin='mobile'))
        with self.assertRaises(StoreConflict):wrong.clear('request',True)
        same_device_wrong_origin=AccountDataStore(self.store,SimpleNamespace(owner_id='owner',device_id='desktop',origin='mobile'))
        with self.assertRaises(StoreConflict):same_device_wrong_origin.clear('request',True)
        with self.assertRaises(DeviceUnavailable):self.cleanup.clear('new-request',True)
        with self.sessions() as db:
            self.assertEqual(AccountDataStore(SQLStore(db),self.principal).clear('request',True),result)
        self.assertEqual(len(self.rows(DataCleanupReceipt)),1)

    def test_confirmation_invalid_request_and_first_revoked_device_rejected(self):
        before=self.snapshot('owner')
        for request,confirm in [('request',False),('request',1),('request','true'),('',True),('x'*161,True),('bad\nrequest',True)]:
            with self.assertRaises(ValueError):self.cleanup.clear(request,confirm)
        revoked=AccountDataStore(self.store,SimpleNamespace(owner_id='owner',device_id='old',origin='desktop'))
        with self.assertRaises(DeviceUnavailable):revoked.clear('request',True)
        missing=AccountDataStore(self.store,SimpleNamespace(owner_id='owner',device_id='missing',origin='desktop'))
        with self.assertRaises(DeviceUnavailable):missing.clear('request',True)
        self.assertEqual(self.snapshot('owner'),before)

    def test_revocation_new_login_optout_and_reservations_prevent_resurrection(self):
        self.cleanup.clear('request',True)
        with self.assertRaises((DeviceUnavailable,SessionDeleted)):self.store.finish_model('owner','desktop','pending-session','pending','pending-attempt','query',{'text':'must not resurrect'})
        with self.assertRaises(DeviceUnavailable):self.store.save_turn('owner','desktop','fresh','fresh','query',{})
        with self.assertRaises(DeviceUnavailable):PresetStore(self.store,self.principal).changes()
        self.store.save_device(Device(owner_id='owner',device_id='fresh-login',origin='desktop'))
        fresh=SimpleNamespace(owner_id='owner',device_id='fresh-login',origin='desktop')
        with self.assertRaises(PresetSyncDisabled):PresetStore(self.store,fresh).put('fresh',{'name':'new','pattern':'{date}','isDefault':False},0)
        event={'eventId':'fresh','sequence':2,'payload':{}}
        with self.assertRaises(SyncForbidden):self.store.ingest_batch('owner','fresh-login',[event])
        for state in ('completed','pending','uncertain'):
            with self.assertRaises(SessionDeleted):self.store.reserve_model('owner','fresh-login',state+'-session',state,'query')
        saved=self.store.save_turn('owner','fresh-login','fresh-session','fresh-turn','query',{})
        self.assertFalse(saved['persisted'])
        self.assertEqual(self.rows(AgentTurn),[])
        self.assertEqual({r.state for r in self.rows(AgentModelRequest)},{'completed_unretained','pending','uncertain'})

    def test_failure_at_commit_rolls_back_every_table_atomically(self):
        before=self.snapshot('owner');other=self.snapshot('other')
        with patch.object(self.db,'commit',side_effect=RuntimeError('planned commit failure')):
            with self.assertRaisesRegex(RuntimeError,'planned commit failure'):self.cleanup.clear('request',True)
        self.assertEqual(self.snapshot('owner'),before)
        self.assertEqual(self.snapshot('other'),other)
        self.assertEqual(self.rows(DataCleanupReceipt),[])
        self.assertTrue(self.cleanup.clear('request',True)['cleared'])

    def test_rpc_binding_and_confirmation_never_calls_network(self):
        backend=MagicMock();backend.request.return_value={'cleared':True,'counts':{},'devicesRevoked':1,'retainedSecurityMetadata':[]}
        helper=AccountDataStore(backend,self.principal)
        result=helper.clear('request',True)
        self.assertTrue(result['cleared'])
        self.assertEqual(backend.request.call_args.args,('rpc/yingxu_clear_sync_data','POST'))
        self.assertEqual(backend.request.call_args.kwargs['body'],{'p_owner':'owner','p_device':'desktop','p_origin':'desktop','p_request':'request','p_confirmed':True})
        backend.request.reset_mock()
        with self.assertRaises(ValueError):helper.clear('request',False)
        backend.request.assert_not_called()


if __name__=='__main__':unittest.main()
