"""Temporary SQLite only; actual atomic guard and owner-cleanup boundary."""
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import object_session,sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Base
from app.models import AgentAction,Device,utcnow
from app.store import SQLStore,StoreConflict,DeviceUnavailable
from app.action_commit import commit_action
from app.account_data import AccountDataStore
from app.sync_snapshots import SyncSnapshotLease
from app.local_step_store import LocalStep


class ActionCommit(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/actions.sqlite')
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.db.add_all([Device(owner_id='owner',device_id='desktop',origin='desktop'),Device(owner_id='other',device_id='desktop',origin='desktop'),
                         Device(owner_id='owner',device_id='mobile',origin='mobile')]);self.db.commit()
    def tearDown(self):self.db.close();self.engine.dispose();self.temp.cleanup()
    def action(self,**changes):
        fields={'id':'action,"x\\y','owner_id':'owner','device_id':'desktop','origin':'desktop','tool':'rename_item','state':'awaiting_local_confirmation',
                'proposal_json':{'fixture':True},'expires_at':utcnow()+timedelta(minutes=5)}
        fields.update(changes);return AgentAction(**fields)

    def test_actual_guard_owner_lock_and_existing_initial_states(self):
        for state in ('proposed','awaiting_local_confirmation',None):
            row=self.action(id=str(state),state=state)
            original=self.store._add
            def checked_add(candidate):
                self.assertIsNotNone(self.db.execute(select(Device).where(Device.owner_id=='owner')).first())
                self.assertTrue(self.db.in_transaction())
                return original(candidate)
            with patch.object(self.store,'_lock_owner',wraps=self.store._lock_owner) as owner_lock,patch.object(self.store,'_add',side_effect=checked_add):
                self.assertIsNone(commit_action(self.store,row))
            owner_lock.assert_called_once_with('owner')
            self.assertEqual(self.store.action('owner',row.id).state,state or 'proposed')

    def test_duplicate_conflict_preserves_original_and_transaction_reusable(self):
        original=self.action();commit_action(self.store,original)
        # The RPC adapter serializes the row without attaching it to this ORM
        # session; the native SQL adapter does, so detach only when applicable.
        if object_session(original) is self.db:self.db.expunge(original)
        with self.assertRaises(StoreConflict):commit_action(self.store,self.action(proposal_json={'changed':True}))
        self.assertEqual(self.store.action('owner',original.id).proposal_json,{'fixture':True})
        commit_action(self.store,self.action(id='after-conflict'))
        self.assertEqual(len(self.db.scalars(select(AgentAction)).all()),2)

    def test_cross_owner_same_id_isolated_and_missing_device_denied(self):
        commit_action(self.store,self.action())
        commit_action(self.store,self.action(owner_id='other',proposal_json={'other':True}))
        self.assertEqual(self.store.action('owner','action,"x\\y').proposal_json,{'fixture':True})
        self.assertEqual(self.store.action('other','action,"x\\y').proposal_json,{'other':True})
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,self.action(owner_id='missing-owner'))
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,self.action(id='missing',device_id='missing-device'))
        self.assertEqual(len(self.db.scalars(select(AgentAction)).all()),2)

    def test_revocation_origin_mismatch_and_stale_device_object_rejected(self):
        stale=self.store.device('owner','desktop')
        self.db.commit()
        with self.sessions.begin() as db:db.scalar(select(Device).where(Device.owner_id=='owner',Device.device_id=='desktop')).revoked=True
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,self.action())
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,self.action(id='origin',device_id='mobile',origin='desktop'))
        self.assertEqual(self.db.scalars(select(AgentAction)).all(),[])
        self.assertTrue(stale.revoked)

    def test_clear_completed_before_late_insert_cannot_resurrect_proposal(self):
        late=self.action(proposal_json={'private':'must not reappear'})
        commit_action(self.store,self.action(id='old',proposal_json={'private':'old content'}))
        cleaner=AccountDataStore(self.store,SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop'))
        self.assertTrue(cleaner.clear('clear-request',True)['cleared'])
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,late)
        self.assertEqual(self.db.scalars(select(AgentAction).where(AgentAction.owner_id=='owner')).all(),[])

    def test_add_failure_rolls_back_owner_lock_and_pending_insert(self):
        with patch.object(self.store,'_add',side_effect=RuntimeError('planned persistence failure')):
            with self.assertRaises(RuntimeError):commit_action(self.store,self.action())
        self.assertFalse(self.db.in_transaction())
        self.assertEqual(self.db.scalars(select(AgentAction)).all(),[])
        commit_action(self.store,self.action())

    def test_basic_validation_does_not_accept_transition_states_or_non_json(self):
        for changes in ({'state':'queued'},{'state':'running'},{'state':'succeeded'},{'proposal_json':[]},
                        {'proposal_json':{'invalid':float('nan')}},{'owner_id':''},{'origin':'web'},{'tool':'x'*65}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):commit_action(self.store,self.action(**changes))
        self.assertEqual(self.db.scalars(select(AgentAction)).all(),[])

    def test_rpc_uses_bound_actor_and_exact_existing_payload_fields(self):
        backend=MagicMock();row=self.action()
        self.assertIsNone(commit_action(backend,row))
        call=backend.request.call_args
        self.assertEqual(call.args,('rpc/yingxu_add_action','POST'))
        body=call.kwargs['body']
        self.assertEqual({k:body[k] for k in ('p_owner','p_device','p_origin')},{'p_owner':'owner','p_device':'desktop','p_origin':'desktop'})
        self.assertEqual(set(body['p_action']),{'id','tool','state','proposal_json','expires_at'})
        self.assertEqual(body['p_action']['proposal_json'],{'fixture':True})
        self.assertEqual(body['p_action']['expires_at'],row.expires_at.isoformat())
        backend.request.side_effect=StoreConflict()
        with self.assertRaises(StoreConflict):commit_action(backend,self.action())


if __name__=='__main__':unittest.main()
