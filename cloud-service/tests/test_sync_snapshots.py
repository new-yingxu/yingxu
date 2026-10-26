"""Actual local snapshot leases; optional PG/RPC only on validator Unix sockets."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,select,text,delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
from app.db import Base
from app.models import Device,SyncEvent,utcnow
from app.store import SQLStore,DeviceUnavailable
from app.sync_snapshots import SnapshotStore,SyncSnapshotLease,SnapshotNotFound,SnapshotExpired,SnapshotLimit,SNAPSHOT_DEVICE_LIMIT


class RPCAdapter:
    def __init__(self,db):self.db=db
    def request(self,path,method,body):
        assert path=='rpc/yingxu_sync_snapshot' and method=='POST'
        named=','.join(key+'=>:'+key for key in body)
        try:
            self.db.execute(text('SET LOCAL ROLE service_role'))
            result=self.db.execute(text('SELECT public.yingxu_sync_snapshot('+named+')'),body).scalar()
            self.db.commit();return result
        except DBAPIError as exc:
            self.db.rollback()
            if getattr(exc.orig,'sqlstate',None)=='PT403':raise DeviceUnavailable() from None
            if getattr(exc.orig,'sqlstate',None)=='PT422':raise ValueError('invalid snapshot') from None
            raise


class SnapshotContracts:
    rpc=False
    def service(self,owner=None,device='device',origin='desktop'):
        principal=SimpleNamespace(owner_id=owner or self.owner,device_id=device,origin=origin)
        return SnapshotStore(RPCAdapter(self.db) if self.rpc else self.store,principal)
    def event(self,number,owner=None,paths=False):
        body={'eventId':'event-'+str(number),'payload':{'jobId':'job-'+str(number),'name':'Fixture'}}
        if paths:body['payload']['paths']=['PRIVATE_PATH']
        with self.sessions.begin() as db:
            row=SyncEvent(owner_id=owner or self.owner,device_id='device',event_id=body['eventId'],sequence=number,body_json=body)
            db.add(row);db.flush();identifier=row.id
        return identifier

    def test_fixed_watermark_excludes_new_events_and_supports_incremental_handoff(self):
        first=self.event(1);self.event(2,owner=self.other);second=self.event(3)
        lease=self.service().begin();self.assertEqual(lease['throughCursor'],second)
        late=self.event(4)
        page1=self.service().page(lease['snapshotId'],0,1)
        self.assertEqual([e['eventId'] for e in page1['events']],['event-1']);self.assertTrue(page1['hasMore'])
        page2=self.service().page(lease['snapshotId'],page1['nextCursor'],1)
        self.assertEqual([e['eventId'] for e in page2['events']],['event-3']);self.assertFalse(page2['hasMore'])
        self.assertEqual(page2['throughCursor'],second);self.assertEqual(page1['nextCursor'],first)
        delta=self.store.events(self.owner,after=lease['throughCursor'])
        self.assertEqual([row.id for row in delta],[late]);self.db.rollback()

    def test_snapshot_never_retains_old_private_payload(self):
        self.event(1,paths=True);lease=self.service().begin()
        with self.sessions.begin() as db:
            row=db.scalar(select(SyncEvent).where(SyncEvent.owner_id==self.owner))
            row.body_json={'eventId':'event-1','payload':{'jobId':'job-1','name':'Redacted'}}
        page=self.service().page(lease['snapshotId'])
        self.assertNotIn('PRIVATE_PATH',str(page));self.assertEqual(page['events'][0]['payload']['name'],'Redacted')
        with self.sessions.begin() as db:db.execute(delete(SyncEvent).where(SyncEvent.owner_id==self.owner))
        self.assertEqual(self.service().page(lease['snapshotId'])['events'],[])
        with self.sessions() as db:
            lease_rows=db.scalars(select(SyncSnapshotLease).where(SyncSnapshotLease.owner_id==self.owner)).all()
            self.assertFalse(any(hasattr(row,'body_json') for row in lease_rows))

    def test_owner_device_origin_and_live_revocation_isolation(self):
        self.event(1);lease=self.service().begin()
        for service in (self.service(self.other),self.service(device='second')):
            with self.assertRaises(SnapshotNotFound):service.page(lease['snapshotId'])
        for service in (self.service(device='missing'),self.service(origin='mobile')):
            with self.assertRaises(DeviceUnavailable):service.begin()
            with self.assertRaises(DeviceUnavailable):service.page(lease['snapshotId'])
        self.store.device(self.owner,'device')
        with self.sessions.begin() as db:db.scalar(select(Device).where(Device.owner_id==self.owner,Device.device_id=='device')).revoked=True
        for call in (lambda:self.service().begin(),lambda:self.service().page(lease['snapshotId'])):
            with self.assertRaises(DeviceUnavailable):call()

    def test_expiration_and_device_lease_limit_cleanup(self):
        leases=[self.service().begin() for _ in range(SNAPSHOT_DEVICE_LIMIT)]
        with self.assertRaises(SnapshotLimit):self.service().begin()
        with self.sessions.begin() as db:
            row=db.get(SyncSnapshotLease,leases[0]['snapshotId']);row.expires_at=utcnow()-timedelta(seconds=1)
        with self.assertRaises(SnapshotExpired):self.service().page(leases[0]['snapshotId'])
        replacement=self.service().begin();self.assertNotEqual(replacement['snapshotId'],leases[0]['snapshotId'])
        with self.assertRaises(SnapshotNotFound):self.service().page(leases[0]['snapshotId'])

    def test_empty_snapshot_and_strict_boundary_cursor_limit_uuid(self):
        lease=self.service().begin();page=self.service().page(lease['snapshotId'])
        self.assertEqual(lease['throughCursor'],0);self.assertEqual(page['events'],[])
        self.assertEqual(page['nextCursor'],0);self.assertFalse(page['hasMore'])
        for after,limit in [(-1,1),(True,1),(2**63,1),(0,0),(0,501),(0,True),(1,1)]:
            with self.assertRaises(ValueError):self.service().page(lease['snapshotId'],after,limit)
        for invalid in ('bad','','AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA',None,123):
            with self.assertRaises(ValueError):self.service().page(invalid)
        with self.assertRaises(SnapshotNotFound):self.service().page(str(uuid4()))
        self.assertEqual(self.service().page(lease['snapshotId'],0,500)['scope'],'synced_job_events')

    def test_rpc_server_principal_binding_and_semantic_errors(self):
        store=MagicMock();principal=SimpleNamespace(owner_id=self.owner,device_id='device',origin='desktop')
        service=SnapshotStore(store,principal)
        store.request.return_value={'snapshotId':str(uuid4()),'throughCursor':0,'expiresAt':'2026-01-01T08:00:00+08:00','scope':'synced_job_events'}
        result=service.begin();self.assertEqual(result['expiresAt'],'2026-01-01T00:00:00+00:00')
        self.assertEqual(store.request.call_args.kwargs['body'],{'p_owner':self.owner,'p_device':'device','p_origin':'desktop','p_operation':'begin'})
        for message,error in [('SNAPSHOT_LIMIT',SnapshotLimit),('SNAPSHOT_EXPIRED',SnapshotExpired),('SNAPSHOT_NOT_FOUND',SnapshotNotFound)]:
            store.request.return_value={'error':message}
            with self.assertRaises(error):service.page(str(uuid4()))
        store.request.reset_mock()
        with self.assertRaises(ValueError):service.page('invalid')
        store.request.assert_not_called()


class SQLiteSnapshots(SnapshotContracts,unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/snapshots.sqlite')
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.owner='snapshot-owner';self.other='snapshot-other'
        with self.sessions.begin() as db:
            db.add_all([Device(owner_id=self.owner,device_id='device',origin='desktop'),Device(owner_id=self.owner,device_id='second',origin='desktop'),Device(owner_id=self.other,device_id='device',origin='desktop')])
        self.db=self.sessions();self.store=SQLStore(self.db)
    def tearDown(self):self.db.close();self.engine.dispose();self.temp.cleanup()


class PostgreSQLFixture:
    def setUp(self):
        self.engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        if not str(self.engine.url.query.get('host','')).startswith('/tmp/yingxu-pg-'):
            self.engine.dispose();raise RuntimeError('Only validator Unix socket clusters allowed')
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.owner='snapshot-'+uuid4().hex;self.other='snapshot-'+uuid4().hex
        with self.sessions.begin() as db:
            db.add_all([Device(owner_id=self.owner,device_id='device',origin='desktop'),Device(owner_id=self.owner,device_id='second',origin='desktop'),Device(owner_id=self.other,device_id='device',origin='desktop')])
        self.db=self.sessions();self.store=SQLStore(self.db)
    def test_begin_waits_for_uncommitted_owner_event_before_freezing_watermark(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        tag='snapshot-wait-'+uuid4().hex
        writer=self.sessions()
        SQLStore(writer)._lock_owner(self.owner)
        pending=SyncEvent(owner_id=self.owner,device_id='device',event_id='pending-event',sequence=100,
                          body_json={'eventId':'pending-event','payload':{'jobId':'pending'}})
        writer.add(pending);writer.flush();pending_id=pending.id
        def freeze():
            with self.sessions() as db:
                db.execute(text("SELECT set_config('application_name',:tag,false)"),{'tag':tag});db.commit()
                return SnapshotStore(RPCAdapter(db) if self.rpc else SQLStore(db),SimpleNamespace(owner_id=self.owner,device_id='device',origin='desktop')).begin()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(freeze)
                try:
                    blocked=False;deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        with self.engine.connect() as observer:
                            blocked=bool(observer.execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:tag AND wait_event_type='Lock'"),{'tag':tag}).scalar())
                        if blocked:break
                        if future.done():break
                        time.sleep(0.01)
                    self.assertTrue(blocked,'snapshot begin must wait on the actual owner transaction lock')
                    self.assertFalse(future.done())
                finally:
                    writer.commit()
                lease=future.result(timeout=5)
            self.assertEqual(lease['throughCursor'],pending_id)
            self.assertEqual(self.service().page(lease['snapshotId'])['events'][0]['eventId'],'pending-event')
        finally:
            writer.rollback();writer.close()

    def tearDown(self):
        self.db.close()
        with self.engine.begin() as db:
            for table in ('sync_snapshot_leases','sync_events','devices','sync_owner_locks'):
                db.execute(text('DELETE FROM '+table+' WHERE owner_id IN (:owner,:other)'),{'owner':self.owner,'other':self.other})
        self.engine.dispose()


@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class PostgreSQLSnapshots(PostgreSQLFixture,SnapshotContracts,unittest.TestCase):pass

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class PostgreSQLRPCSnapshots(PostgreSQLFixture,SnapshotContracts,unittest.TestCase):rpc=True

if __name__=='__main__':unittest.main()
