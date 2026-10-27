"""Opt-in contracts against an isolated local PostgreSQL cluster, never CloudBase."""
import json
import os
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.models import Device,SyncEvent,AgentAction,SyncPreferences,AgentTurn,DeletedAgentSession,SyncInvalidation,SyncOwnerLock,AgentModelRequest
from app.store import SQLStore,StoreConflict,DeviceUnavailable,SyncForbidden,SessionDeleted
from test_store_contract import PersistenceContract


class PostgreSQLRPCStore(SQLStore):
    """Run the actual migration RPCs over SQL; this does not test HTTP transport."""
    def call(self, sql, args):
        try:
            self.db.execute(text('SET LOCAL ROLE service_role'))
            result=self.db.execute(text(sql),args).scalar()
            self.db.commit()
            self.db.expire_all()
            return result
        except DBAPIError as exc:
            self.db.rollback()
            code=getattr(exc.orig,'sqlstate',None)
            if code=='PT409': raise StoreConflict() from None
            if code=='PT410': raise SessionDeleted() from None
            if code=='PT403':
                message=str(exc.orig).splitlines()[0]
                if message=='DEVICE_REVOKED_OR_UNREGISTERED': raise DeviceUnavailable() from None
                raise SyncForbidden(message) from None
            raise

    def ingest_batch(self,owner,device,events):
        return self.call('SELECT public.yingxu_ingest_batch(:owner,:device,CAST(:events AS jsonb))',dict(owner=owner,device=device,events=json.dumps(events)))

    def set_preferences(self,owner,values):
        return self.call('SELECT public.yingxu_set_preferences(:owner,:sync_records,:sync_agent_sessions,:sync_thumbnails,:sync_paths)',dict(owner=owner,**values))

    def save_turn(self,owner,device,session,turn,query,response):
        return self.call('SELECT public.yingxu_save_turn(:owner,:device,:session,:turn,:query,CAST(:response AS jsonb))',dict(owner=owner,device=device,session=session,turn=turn,query=query,response=json.dumps(response)))

    def reserve_model(self,owner,device,session,turn,query):
        from uuid import uuid4
        return self.call('SELECT public.yingxu_reserve_model(:owner,:device,:session,:turn,:query,:attempt)',dict(owner=owner,device=device,session=session,turn=turn,query=query,attempt=str(uuid4())))

    def finish_model(self,owner,device,session,turn,attempt,query,response):
        return self.call('SELECT public.yingxu_finish_model(:owner,:device,:session,:turn,:attempt,:query,CAST(:response AS jsonb))',dict(owner=owner,device=device,session=session,turn=turn,query=query,attempt=attempt,response=json.dumps(response) if response is not None else None))

    def delete_session(self,owner,device,session):
        return self.call('SELECT public.yingxu_delete_session(:owner,:device,:session)',dict(owner=owner,device=device,session=session))


@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class PostgreSQLContract(PersistenceContract,unittest.TestCase):
    store_type=SQLStore

    def setUp(self):
        self.fixture()
        self.engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        if not str(self.engine.url.query.get('host') or self.engine.url.host or '').startswith('/tmp/yingxu-pg-'):
            self.engine.dispose()
            raise RuntimeError('Only validator-created isolated Unix socket clusters are allowed')
        self.store=self.store_type(Session(self.engine))
        self.assertTrue(self.store.ready(production=True))

    def test_late_model_completion_preserves_reservation_on_rejection(self):
        self.store.save_device(self.device(self.owner))
        held=self.store.reserve_model(self.owner,self.device_id,'deleted-pending','turn','question')
        self.store.delete_session(self.owner,self.device_id,'deleted-pending')
        with self.assertRaises(SessionDeleted):
            self.store.finish_model(self.owner,self.device_id,'deleted-pending','turn',held['attemptId'],'question',{'answer':'late'})
        with self.engine.connect() as connection:
            state=connection.execute(text("SELECT state FROM agent_model_requests WHERE owner_id=:owner AND session_id='deleted-pending'"),{'owner':self.owner}).scalar()
        self.assertEqual(state,'pending')
        self.assertEqual(self.store.turns(self.owner,'deleted-pending'),[])
        with self.assertRaises(SessionDeleted):
            self.store.reserve_model(self.owner,self.device_id,'deleted-pending','turn','question')

    def test_cached_device_refreshes_after_external_revocation(self):
        self.store.save_device(self.device(self.owner))
        cached=self.store.device(self.owner,self.device_id)
        self.assertFalse(cached.revoked)
        with Session(self.engine) as external:
            external.execute(text('UPDATE devices SET revoked=TRUE WHERE owner_id=:owner'),{'owner':self.owner})
            external.commit()
        self.assertTrue(self.store.device(self.owner,self.device_id).revoked)
        with self.assertRaises(DeviceUnavailable):
            self.store.reserve_model(self.owner,self.device_id,'revoked','turn','question')

    def test_concurrent_model_reservations_dispatch_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.store.save_device(self.device(self.owner))
        ready=Barrier(2)
        def reserve():
            store=self.store_type(Session(self.engine))
            try:
                ready.wait(timeout=3)
                return store.reserve_model(self.owner,self.device_id,'concurrent','turn','question')
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(reserve)
            second=pool.submit(reserve)
            results=[first.result(timeout=5),second.result(timeout=5)]
        self.assertEqual(sorted(r['state'] for r in results),['pending','reserved'])

    def test_direct_sql_and_rpc_share_request_fingerprint(self):
        self.store.save_device(self.device(self.owner))
        flags=dict(sync_records=True,sync_agent_sessions=True,sync_thumbnails=False,sync_paths=True)
        self.store.set_preferences(self.owner,flags)
        event={'eventId':'mixed-stores','deviceId':self.device_id,'sequence':1,'payload':{'name':'你好','source':'/synthetic/source'}}
        self.store.ingest_batch(self.owner,self.device_id,[event])
        counterpart_type=PostgreSQLRPCStore if self.store_type is SQLStore else SQLStore
        counterpart=counterpart_type(Session(self.engine))
        try:
            counterpart.set_preferences(self.owner,{**flags,'sync_paths':False})
            replay=counterpart.ingest_batch(self.owner,self.device_id,[event])
            self.assertEqual(replay['duplicateEventIds'],['mixed-stores'])
        finally:
            counterpart.close()

    def test_owner_lock_orders_publication_across_real_transactions(self):
        from concurrent.futures import ThreadPoolExecutor
        from queue import Queue
        import time
        self.store.save_device(self.device(self.owner))
        self.store._lock_owner(self.owner)
        first=SyncEvent(owner_id=self.owner,device_id=self.device_id,event_id='held-first',sequence=1,body_json={'payload':{}})
        self.store.db.add(first)
        self.store.db.flush()
        pids=Queue()
        def publish():
            store=self.store_type(Session(self.engine))
            try:
                pids.put(store.db.execute(text('SELECT pg_backend_pid()')).scalar())
                return store.ingest_batch(self.owner,self.device_id,[{'eventId':'second','deviceId':self.device_id,'sequence':2,'payload':{}}])
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(publish)
            try:
                pid=pids.get(timeout=3)
                deadline=time.monotonic()+3
                waiting=False
                while time.monotonic()<deadline:
                    with self.engine.connect() as connection:
                        waiting=connection.execute(text('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)'),{'pid':pid}).scalar()
                    if waiting:break
                    time.sleep(.02)
                self.assertTrue(waiting,'Publisher must be blocked on the held owner lock')
                self.assertFalse(pending.done())
                with Session(self.engine) as reader:
                    self.assertEqual(SQLStore(reader).events(self.owner),[])
            finally:
                self.store.db.commit()
            self.assertEqual(pending.result(timeout=5)['acceptedEventIds'],['second'])
        self.assertEqual([r.event_id for r in self.store.events(self.owner)],['held-first','second'])

    def tearDown(self):
        self.store.close()
        with Session(self.engine) as db:
            for model in (AgentModelRequest,AgentTurn,DeletedAgentSession,SyncInvalidation,SyncEvent,AgentAction,SyncPreferences,Device,SyncOwnerLock):
                db.query(model).filter(model.owner_id.in_([self.owner,self.other])).delete(synchronize_session=False)
            db.commit()
        self.engine.dispose()


@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class PostgreSQLRPCContract(PostgreSQLContract):
    store_type=PostgreSQLRPCStore
