"""Opt-in v15 native/RPC contracts; only validator-created local Unix sockets."""
import json
import os
from pathlib import Path
import re
import sys
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text,select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.models import Device
from app.store import SQLStore,DeviceUnavailable,SessionDeleted,StoreConflict
from app.local_step_store import LocalStep,LocalStepStore
import test_local_steps as cases


def isolated_pg_url():
    value=os.getenv('YINGXU_TEST_PG_URL')
    if not value:return None
    try:
        url=make_url(value);host=url.query.get('host') or url.host
        if (url.drivername.startswith('postgresql') and isinstance(host,str)
            and re.fullmatch(r'/tmp/yingxu-pg-[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*',host)):
            return value
    except (ValueError,TypeError):pass
    return None


PG_URL=isolated_pg_url()
CLEANUP_TABLES=('local_agent_steps','data_cleanup_receipts','sync_snapshot_leases','agent_graph_pending_writes','agent_graph_checkpoints',
                'agent_model_requests','agent_turns','agent_actions','preset_revisions','sync_events','deleted_agent_sessions',
                'sync_invalidations','sync_preferences','devices','sync_owner_locks')


def erase_fixtures(engine):
    with engine.begin() as db:
        for table in CLEANUP_TABLES:
            db.execute(text('DELETE FROM public.'+table+" WHERE owner_id IN ('owner','other')"))


class RPCAdapter:
    """Calls only fixed local migration RPCs; never HTTP/PostgREST/network."""
    def __init__(self,db):self.db=db
    def request(self,path,method,body):
        if path!='rpc/yingxu_local_agent_step' or method!='POST':raise AssertionError('unexpected local-step RPC')
        args={**body,'p_payload':json.dumps(body['p_payload'],allow_nan=False)}
        sql='SELECT public.yingxu_local_agent_step(:p_owner,:p_device,:p_origin,:p_operation,:p_session,:p_turn,:p_step,CAST(:p_payload AS jsonb))'
        try:
            self.db.execute(text('SET LOCAL ROLE service_role'))
            result=self.db.execute(text(sql),args).scalar()
            self.db.commit();self.db.expire_all();return result
        except Exception as exc:
            self.db.rollback()
            code=getattr(getattr(exc,'orig',None),'sqlstate',None)
            if code=='PT403':raise DeviceUnavailable() from None
            if code=='PT410':raise SessionDeleted() from None
            if code=='PT409':raise StoreConflict() from None
            raise


class Fixture:
    rpc=False
    def setUp(self):
        if not PG_URL:self.skipTest('Only /tmp/yingxu-pg- validator socket PG is allowed')
        self.engine=create_engine(PG_URL)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        erase_fixtures(self.engine)
        self.db=self.sessions()
        self.store=RPCAdapter(self.db) if self.rpc else SQLStore(self.db)
        self.p=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.helper=LocalStepStore(self.store,self.p)
        for owner,device,origin in [('owner','desktop','desktop'),('owner','desktop2','desktop'),('other','desktop','desktop'),('owner','mobile','mobile')]:
            self.db.add(Device(owner_id=owner,device_id=device,origin=origin))
        self.db.commit();self.root='a'*64;self.input='b'*64
    def tearDown(self):
        self.db.close()
        try:erase_fixtures(self.engine)
        finally:self.engine.dispose()

    def test_concurrent_same_step_reserves_exactly_one_attempt(self):
        count=4;barrier=Barrier(count)
        def reserve(_):
            with self.sessions() as db:
                helper=LocalStepStore(RPCAdapter(db) if self.rpc else SQLStore(db),self.p)
                barrier.wait(timeout=10)
                return helper.reserve('session','turn',0,self.root,self.input)
        with ThreadPoolExecutor(max_workers=count) as workers:results=list(workers.map(reserve,range(count)))
        self.assertEqual([r['state'] for r in results].count('reserved'),1)
        self.assertEqual([r['state'] for r in results].count('pending'),count-1)
        self.assertTrue(all(r=={'state':'pending','retained':False} for r in results if r['state']=='pending'))
        rows=self.db.scalars(select(LocalStep)).all();self.assertEqual(len(rows),1)
        self.assertEqual(self.reserve(),{'state':'pending','retained':False})
        reserved=next(r for r in results if r['state']=='reserved')
        self.finish(reserved['attemptId'],state='uncertain',response=None)
        self.assertEqual(self.reserve(),{'state':'uncertain','retained':False})

    def test_actual_preference_rpc_clears_cache_without_reenabling_recovery(self):
        reserved=self.reserve();self.finish(reserved['attemptId'],response={'answer':'must erase'})
        with self.sessions() as db:
            try:
                db.execute(text('SET LOCAL ROLE service_role'))
                db.execute(text('SELECT public.yingxu_set_preferences(:owner,true,false,false,false)'),{'owner':'owner'});db.commit()
            except Exception:db.rollback();raise
        self.assertIsNone(self.row().response_json)
        self.assertEqual(self.reserve(),{'state':'completed','retained':False})
        with self.sessions.begin() as db:db.execute(text("UPDATE public.sync_preferences SET sync_agent_sessions=TRUE WHERE owner_id='owner'"))
        self.assertEqual(self.reserve(),{'state':'completed','retained':False})

    def test_actual_session_delete_rpc_scrubs_cache_and_blocks_late_finish(self):
        reserved=self.reserve();self.finish(reserved['attemptId'],response={'answer':'must erase'})
        with self.sessions() as db:
            try:
                db.execute(text('SET LOCAL ROLE service_role'))
                db.execute(text("SELECT public.yingxu_delete_session('owner','desktop','session')"));db.commit()
            except Exception:db.rollback();raise
        self.assertIsNone(self.row().response_json)
        with self.assertRaises(SessionDeleted):self.reserve()
        with self.assertRaises(SessionDeleted):self.finish(reserved['attemptId'],response={'answer':'late'})


@unittest.skipUnless(PG_URL,'Only validator local socket PostgreSQL is opt-in')
class NativeLocalSteps(Fixture,cases.LocalSteps):pass


@unittest.skipUnless(PG_URL,'Only validator local socket PostgreSQL is opt-in')
class RPCLocalSteps(Fixture,cases.LocalSteps):rpc=True
