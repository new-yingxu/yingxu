"""The same persistence contract runs locally and, explicitly opted in, on CloudBase.

Cloud tests create synthetic owner IDs and delete only those IDs in tearDown.
They do not establish real user authentication or validate sign-in channels.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4
from dataclasses import replace
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.db import Base, build_database
from app.models import Device, SyncEvent, AgentAction, utcnow
from app.graph_checkpoints import GraphCheckpoint, GraphPendingWrite
from app.local_step_store import LocalStep
from app.store import SQLStore, PostgRESTStore, StoreConflict, DeviceUnavailable, SyncForbidden, SessionDeleted


class PersistenceContract:
    def fixture(self):
        self.owner = 'yingxu-store-test-' + uuid4().hex + '-a,"x\\y'
        self.other = 'yingxu-store-test-' + uuid4().hex + '-b'
        self.device_id = 'device,"x\\y'

    def device(self, owner):
        return Device(owner_id=owner, device_id=self.device_id, origin='desktop', display_name='Synthetic fixture')

    def event(self, owner, event_id='event,"x\\y', sequence=1):
        return SyncEvent(owner_id=owner, device_id=self.device_id, event_id=event_id, sequence=sequence,
            body_json={'payload': {'name': 'fixture', 'source': '/synthetic/source', 'paths': ['/synthetic/target']}})

    def action(self, owner):
        return AgentAction(id='action,"x\\y', owner_id=owner, device_id=self.device_id, origin='desktop', tool='rename_item', state='awaiting_local_confirmation', proposal_json={'fixture': True}, expires_at=utcnow()+timedelta(minutes=5))

    def test_owner_filters_and_idempotency(self):
        for owner in (self.owner, self.other):
            self.store.save_device(self.device(owner))
            self.store.add_event(self.event(owner))
        self.assertEqual([x.owner_id for x in self.store.devices(self.owner)], [self.owner])
        self.assertEqual(len(self.store.events(self.owner)), 1)
        existing = self.store.existing_event(self.owner, 'event,"x\\y', self.device_id, 1)
        self.assertEqual(existing.owner_id, self.owner)
        with self.assertRaises(StoreConflict):
            self.store.add_event(self.event(self.owner))
        self.store.add_event(self.event(self.owner, event_id='next', sequence=2))
        first = self.store.events(self.owner, limit=1)
        rest = self.store.events(self.owner, after=first[0].id)
        self.assertEqual([x.event_id for x in rest], ['next'])
        self.assertEqual(self.store.events(self.owner, newest=True, limit=1)[0].event_id, 'next')

    def test_path_redaction_publishes_owner_scoped_invalidation(self):
        flags=dict(sync_records=True,sync_agent_sessions=True,sync_thumbnails=False,sync_paths=True)
        self.store.set_preferences(self.owner,flags)
        self.assertEqual(self.store.invalidations(self.owner),[])
        self.store.add_event(self.event(self.owner))
        self.store.set_preferences(self.owner,{**flags,'sync_paths':False})
        notices=self.store.invalidations(self.owner)
        self.assertEqual([r.kind for r in notices],['paths_redacted'])
        self.assertEqual(self.store.invalidations(self.other),[])
        self.assertEqual(self.store.invalidations(self.owner,after=notices[0].id),[])
        self.store.set_preferences(self.owner,{**flags,'sync_paths':False})
        self.assertEqual(len(self.store.invalidations(self.owner)),1)
        self.store.set_preferences(self.owner,flags)
        self.store.set_preferences(self.owner,{**flags,'sync_paths':False})
        self.assertEqual(len(self.store.invalidations(self.owner)),2)

    def test_preferences_scrub_only_owner(self):
        for owner in (self.owner, self.other):
            self.store.add_event(self.event(owner))
        self.store.set_preferences(self.owner, dict(sync_records=True, sync_agent_sessions=True, sync_thumbnails=False, sync_paths=False))
        self.assertFalse(self.store.preferences(self.owner).sync_paths)
        self.assertNotIn('source', self.store.events(self.owner)[0].body_json['payload'])
        self.assertNotIn('paths', self.store.events(self.owner)[0].body_json['payload'])
        self.assertIn('source', self.store.events(self.other)[0].body_json['payload'])

    def test_session_deletion_prevents_replay_resurrection(self):
        for owner in (self.owner,self.other):
            self.store.save_device(self.device(owner))
            self.store.save_turn(owner,self.device_id,'session','turn','question',{'answer':'fixture'})
        self.store.delete_session(self.owner,self.device_id,'session')
        self.store.delete_session(self.owner,self.device_id,'session')
        self.assertEqual(self.store.turns(self.owner,'session'),[])
        deletions=self.store.deleted_sessions(self.owner)
        self.assertEqual([r.session_id for r in deletions],['session'])
        self.assertEqual(self.store.deleted_sessions(self.owner,after=deletions[0].id),[])
        self.assertEqual(self.store.deleted_sessions(self.other),[])
        self.assertEqual(len(self.store.turns(self.other,'session')),1)
        for turn in ('turn','new-turn'):
            with self.assertRaises(SessionDeleted):
                self.store.save_turn(self.owner,self.device_id,'session',turn,'question',{})
        self.assertTrue(self.store.save_turn(self.owner,self.device_id,'new-session','turn','question',{})['persisted'])

    def test_model_reservation_prevents_duplicate_dispatch(self):
        self.store.save_device(self.device(self.owner))
        first=self.store.reserve_model(self.owner,self.device_id,'model-session','turn','question')
        self.assertEqual(first['state'],'reserved')
        self.assertEqual(self.store.reserve_model(self.owner,self.device_id,'model-session','turn','question')['state'],'pending')
        with self.assertRaises(StoreConflict):self.store.reserve_model(self.owner,self.device_id,'model-session','turn','changed')
        with self.assertRaises(StoreConflict):self.store.save_turn(self.owner,self.device_id,'model-session','turn','question',{})
        result=self.store.finish_model(self.owner,self.device_id,'model-session','turn',first['attemptId'],'question',{'answer':'first','model':'fixture'})
        self.assertTrue(result['persisted'])
        self.assertEqual(self.store.reserve_model(self.owner,self.device_id,'model-session','turn','question')['response']['answer'],'first')
        second=self.store.reserve_model(self.owner,self.device_id,'model-session','uncertain','question')
        self.store.finish_model(self.owner,self.device_id,'model-session','uncertain',second['attemptId'],'question',None)
        self.assertEqual(self.store.reserve_model(self.owner,self.device_id,'model-session','uncertain','question')['state'],'uncertain')
        self.store.set_preferences(self.owner,dict(sync_records=True,sync_agent_sessions=False,sync_thumbnails=False,sync_paths=False))
        third=self.store.reserve_model(self.owner,self.device_id,'model-session','private','private question')
        result=self.store.finish_model(self.owner,self.device_id,'model-session','private',third['attemptId'],'private question',{'answer':'not-retained'})
        self.assertFalse(result['persisted'])
        self.assertEqual(self.store.reserve_model(self.owner,self.device_id,'model-session','private','private question')['state'],'completed_unretained')
        self.assertEqual(len(self.store.turns(self.owner,'model-session')),1)
        pending=self.store.reserve_model(self.owner,self.device_id,'model-session','delete-pending','question')
        self.store.delete_session(self.owner,self.device_id,'model-session')
        with self.assertRaises(SessionDeleted):self.store.finish_model(self.owner,self.device_id,'model-session','delete-pending',pending['attemptId'],'question',{'answer':'late'})
        self.assertEqual(self.store.turns(self.owner,'model-session'),[])
        with self.assertRaises(SessionDeleted):self.store.reserve_model(self.owner,self.device_id,'model-session','turn','question')

    def test_agent_turn_history_idempotency_and_opt_out(self):
        self.store.save_device(self.device(self.owner))
        first=self.store.save_turn(self.owner,self.device_id,'session','turn','question',{'answer':'first'})
        self.assertTrue(first['persisted'])
        replay=self.store.save_turn(self.owner,self.device_id,'session','turn','question',{'answer':'different result'})
        self.assertEqual(replay['response'],{'answer':'first'})
        self.assertTrue(replay['duplicate'])
        with self.assertRaises(StoreConflict):
            self.store.save_turn(self.owner,self.device_id,'session','turn','different question',{})
        self.assertEqual(self.store.turns(self.other,'session'),[])
        self.assertEqual(self.store.turns(self.owner,'another-session'),[])
        rows=self.store.turns(self.owner,'session')
        self.assertEqual(len(rows),1)
        self.assertEqual(self.store.turns(self.owner,'session',after=rows[0].id),[])
        self.store.set_preferences(self.owner,dict(sync_records=True,sync_agent_sessions=False,sync_thumbnails=False,sync_paths=False))
        result=self.store.save_turn(self.owner,self.device_id,'session','not-saved','private question',{})
        self.assertFalse(result['persisted'])
        self.assertEqual(len(self.store.turns(self.owner,'session')),1)
        self.store.revoke_device(self.store.device(self.owner,self.device_id))
        with self.assertRaises(DeviceUnavailable):
            self.store.save_turn(self.owner,self.device_id,'session','turn','question',{})

    def test_action_compare_and_swap(self):
        for owner in (self.owner, self.other):
            self.store.save_device(self.device(owner))
        for owner in (self.owner, self.other):
            self.store.add_action(self.action(owner))
        a = self.store.action(self.owner, 'action,"x\\y')
        self.store.transition_action(a, 'queued')
        stale = self.action(self.owner)
        with self.assertRaises(StoreConflict):
            self.store.transition_action(stale, 'declined')
        self.assertEqual(self.store.action(self.owner, a.id).state, 'queued')
        self.assertEqual(self.store.action(self.other, a.id).state, 'awaiting_local_confirmation')
        self.store.transition_action(a,'running')
        self.store.transition_action(a,'succeeded','VERIFIED')
        self.assertEqual(self.store.action(self.owner,a.id).result_code,'VERIFIED')

    def test_revoked_device_cannot_be_touched_or_reenabled(self):
        row = self.device(self.owner)
        self.store.save_device(row)
        self.store.revoke_device(row)
        with self.assertRaises(DeviceUnavailable):
            self.store.touch_device(row)
        self.assertTrue(self.store.device(self.owner, self.device_id).revoked)

    def test_path_redaction_preserves_original_request_identity(self):
        self.store.save_device(self.device(self.owner))
        flags=dict(sync_records=True,sync_agent_sessions=True,sync_thumbnails=False,sync_paths=True)
        self.store.set_preferences(self.owner,flags)
        event={'eventId':'private-replay','deviceId':self.device_id,'sequence':1,
               'payload':{'name':'fixture','source':'/synthetic/private','paths':['/synthetic/destination']}}
        self.store.ingest_batch(self.owner,self.device_id,[event])
        self.store.set_preferences(self.owner,{**flags,'sync_paths':False})
        self.assertNotIn('source',self.store.events(self.owner)[0].body_json['payload'])
        self.assertEqual(self.store.ingest_batch(self.owner,self.device_id,[event])['duplicateEventIds'],['private-replay'])
        changed={**event,'payload':{**event['payload'],'source':'/synthetic/changed'}}
        with self.assertRaises(StoreConflict):
            self.store.ingest_batch(self.owner,self.device_id,[changed])
        self.store.set_preferences(self.owner,flags)
        self.assertNotIn('source',self.store.events(self.owner)[0].body_json['payload'])
        self.assertEqual(len(self.store.events(self.owner)),1)

    def test_atomic_batch_rollback_replay_and_policy(self):
        self.store.save_device(self.device(self.owner))
        first={'eventId':'atomic-first','deviceId':self.device_id,'sequence':1,'payload':{'name':'synthetic'}}
        collision={**first,'eventId':'atomic-conflict'}
        with self.assertRaises(StoreConflict):
            self.store.ingest_batch(self.owner,self.device_id,[first,collision])
        self.assertEqual(self.store.events(self.owner),[])
        second={**first,'eventId':'atomic-second','sequence':2}
        saved=self.store.ingest_batch(self.owner,self.device_id,[first,second])
        self.assertEqual(saved['acceptedEventIds'],['atomic-first','atomic-second'])
        replay=self.store.ingest_batch(self.owner,self.device_id,[first,second])
        self.assertEqual(replay['duplicateEventIds'],['atomic-first','atomic-second'])
        third={**first,'eventId':'atomic-third','sequence':3}
        with self.assertRaises(StoreConflict):
            self.store.ingest_batch(self.owner,self.device_id,[third,collision])
        self.assertEqual(len(self.store.events(self.owner)),2)
        private={**first,'eventId':'atomic-private','sequence':4,'payload':{'source':'/synthetic/private'}}
        with self.assertRaises(SyncForbidden):
            self.store.ingest_batch(self.owner,self.device_id,[third,private])
        self.assertEqual(len(self.store.events(self.owner)),2)
        self.store.set_preferences(self.owner,dict(sync_records=False,sync_agent_sessions=True,sync_thumbnails=False,sync_paths=False))
        with self.assertRaises(SyncForbidden):
            self.store.ingest_batch(self.owner,self.device_id,[third])
        self.assertEqual(len(self.store.events(self.owner)),2)
        self.assertEqual(self.store.ingest_batch(self.owner,self.device_id,[first])['duplicateEventIds'],['atomic-first'])


class SQLiteContract(PersistenceContract, unittest.TestCase):
    def setUp(self):
        self.fixture()
        self.temp = tempfile.TemporaryDirectory()
        settings = replace(Settings.from_env(), app_env='development', database_url='sqlite:///'+self.temp.name+'/test.db')
        self.engine, sessions = build_database(settings)
        Base.metadata.create_all(self.engine)
        self.store = SQLStore(sessions())

    def tearDown(self):
        self.store.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_uncommitted_earlier_event_blocks_later_publication(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event
        from sqlalchemy.orm import Session
        self.store.save_device(self.device(self.owner))
        self.store._lock_owner(self.owner)
        first=SyncEvent(owner_id=self.owner,device_id=self.device_id,event_id='held-first',sequence=1,body_json={'payload':{}})
        self.store.db.add(first)
        self.store.db.flush()
        entered=Event()
        second={'eventId':'second','deviceId':self.device_id,'sequence':2,'payload':{}}
        def publish():
            store=SQLStore(Session(self.engine))
            try:
                entered.set()
                return store.ingest_batch(self.owner,self.device_id,[second])
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(publish)
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(TimeoutError):
                    pending.result(timeout=0.2)
            finally:
                self.store.db.commit()
            self.assertEqual(pending.result(timeout=5)['acceptedEventIds'],['second'])
        events=self.store.events(self.owner)
        self.assertEqual([x.event_id for x in events],['held-first','second'])


@unittest.skipUnless(os.getenv('YINGXU_RUN_CLOUD_TESTS') == '1', 'CloudBase integration is opt-in')
class CloudBaseContract(PersistenceContract, unittest.TestCase):
    def setUp(self):
        self.fixture()
        self.store = PostgRESTStore(os.environ['CLOUDBASE_ENV_ID'], os.environ['CLOUDBASE_APIKEY'])
        self.assertTrue(self.store.ready(production=True))

    def tearDown(self):
        for owner in (self.owner, self.other):
            assert owner.startswith('yingxu-store-test-')
            for table in ('agent_model_requests', 'sync_invalidations', 'deleted_agent_sessions', 'agent_turns', 'sync_events', 'agent_actions', 'sync_preferences', 'devices', 'sync_owner_locks'):
                self.store.request(table, 'DELETE', self.store._filters(owner))
                self.assertEqual(self.store.request(table, query={**self.store._filters(owner), 'limit': 1}), [])
        self.store.close()


if __name__ == '__main__':
    unittest.main()
