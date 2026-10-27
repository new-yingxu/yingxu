"""Run preset behavioral contracts against actual local PG and service-role RPC."""
import os,json,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
from types import SimpleNamespace
from app.models import Device
from app.store import SQLStore,DeviceUnavailable
from app.preset_store import PresetStore
import test_presets as cases

class RPCAdapter:
    def __init__(self,db):self.db=db
    def request(self,path,method,body):
        assert path=='rpc/yingxu_presets' and method=='POST'
        arguments=dict(body)
        if 'p_body' in arguments:arguments['p_body']=json.dumps(arguments['p_body'])
        named=','.join(key+'=>'+('CAST(:p_body AS jsonb)' if key=='p_body' else ':'+key) for key in arguments)
        try:
            self.db.execute(text('SET LOCAL ROLE service_role'))
            result=self.db.execute(text('SELECT public.yingxu_presets('+named+')'),arguments).scalar()
            self.db.commit();return result
        except DBAPIError as exc:
            self.db.rollback()
            if getattr(exc.orig,'sqlstate',None)=='PT403':raise DeviceUnavailable() from None
            raise

class Fixture:
    rpc=False
    def setUp(self):
        self.engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        if not str(self.engine.url.query.get('host','')).startswith('/tmp/yingxu-pg-'):
            self.engine.dispose();raise RuntimeError('Only validator isolated socket PG allowed')
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        with self.sessions.begin() as db:
            db.add_all([Device(owner_id='owner',device_id='desktop',origin='desktop'),Device(owner_id='other',device_id='desktop',origin='desktop')])
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.principal=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.presets=PresetStore(RPCAdapter(self.db) if self.rpc else self.store,self.principal)
        self.body={'name':'日期序号','pattern':'{date}_{seq}','isDefault':False}
    def test_concurrent_default_writes_leave_one_default(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def write(preset):
            with self.sessions() as db:
                service=PresetStore(RPCAdapter(db) if self.rpc else SQLStore(db),self.principal)
                barrier.wait(timeout=5)
                return service.put(preset,{**self.body,'isDefault':True},0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(write,('concurrent-a','concurrent-b')))
        self.assertEqual(len(results),2)
        feed=self.presets.changes()['changes']
        latest={revision['presetId']:revision for revision in feed}
        self.assertEqual(len(feed),3)
        self.assertEqual(sum(row['body']['isDefault'] for row in latest.values()),1)

    def tearDown(self):
        self.db.close()
        with self.engine.begin() as db:
            for table in ('preset_revisions','sync_preferences','devices','sync_owner_locks'):
                db.execute(text('DELETE FROM '+table+" WHERE owner_id IN ('owner','other')"))
        self.engine.dispose()

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class NativePresets(Fixture,cases.Presets):pass

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class RPCPresets(Fixture,cases.Presets):rpc=True
