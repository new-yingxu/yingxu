"""v16 insertion contracts on isolated validator PG; no HTTP/cloud access."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text,select
from sqlalchemy.orm import sessionmaker
from app.models import Device,AgentAction
from app.store import SQLStore,DeviceUnavailable,SessionDeleted,StoreConflict
from app.action_commit import commit_action
import test_action_commit as cases
import test_local_step_postgresql as bridge


class RPCAdapter:
    def __init__(self,db):self.db=db
    def action(self,owner,action_id):return SQLStore(self.db).action(owner,action_id)
    def device(self,owner,device_id):return SQLStore(self.db).device(owner,device_id)
    def request(self,path,method,body):
        if method!='POST':raise AssertionError('only POST RPC is allowed')
        if path=='rpc/yingxu_add_action':
            args={**body,'p_action':json.dumps(body['p_action'],allow_nan=False)}
            sql='SELECT public.yingxu_add_action(:p_owner,:p_device,:p_origin,CAST(:p_action AS jsonb))'
        elif path=='rpc/yingxu_clear_sync_data':
            args=dict(body)
            sql='SELECT public.yingxu_clear_sync_data(:p_owner,:p_device,:p_origin,:p_request,:p_confirmed)'
        else:raise AssertionError('unexpected fixed action RPC')
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
        if not bridge.PG_URL:self.skipTest('Only /tmp/yingxu-pg- validator socket PG is allowed')
        self.engine=create_engine(bridge.PG_URL)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        bridge.erase_fixtures(self.engine)
        self.db=self.sessions();self.store=RPCAdapter(self.db) if self.rpc else SQLStore(self.db)
        self.db.add_all([Device(owner_id='owner',device_id='desktop',origin='desktop'),Device(owner_id='other',device_id='desktop',origin='desktop'),
                         Device(owner_id='owner',device_id='mobile',origin='mobile')]);self.db.commit()
    def tearDown(self):
        self.db.close()
        try:bridge.erase_fixtures(self.engine)
        finally:self.engine.dispose()


@unittest.skipUnless(bridge.PG_URL,'Only validator local socket PostgreSQL is opt-in')
class NativeActionCommit(Fixture,cases.ActionCommit):pass


@unittest.skipUnless(bridge.PG_URL,'Only validator local socket PostgreSQL is opt-in')
class RPCActionCommit(Fixture,cases.ActionCommit):
    rpc=True
    def test_actual_guard_owner_lock_and_existing_initial_states(self):
        for state in ('proposed','awaiting_local_confirmation',None):
            row=self.action(id=str(state),state=state)
            self.assertIsNone(commit_action(self.store,row))
            self.assertEqual(self.store.action('owner',row.id).state,state or 'proposed')
        self.db.rollback()
        with self.sessions.begin() as db:db.execute(text("UPDATE public.devices SET revoked=TRUE WHERE owner_id='owner' AND device_id='desktop'"))
        with self.assertRaises(DeviceUnavailable):commit_action(self.store,self.action(id='revoked-late'))
        self.assertEqual(len(self.db.scalars(select(AgentAction)).all()),3)

    def test_add_failure_rolls_back_owner_lock_and_pending_insert(self):
        with patch.object(self.db,'commit',side_effect=RuntimeError('planned commit failure')):
            with self.assertRaisesRegex(RuntimeError,'planned commit failure'):commit_action(self.store,self.action())
        self.assertFalse(self.db.in_transaction())
        self.assertEqual(self.db.scalars(select(AgentAction)).all(),[])
        commit_action(self.store,self.action())
        self.assertEqual(len(self.db.scalars(select(AgentAction)).all()),1)
