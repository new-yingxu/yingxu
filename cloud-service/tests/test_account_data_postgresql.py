"""Actual isolated PostgreSQL cleanup, including service-role security-definer RPC."""
import os,unittest
from types import SimpleNamespace
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
import test_account_data as cases
from app.store import SQLStore,StoreConflict,DeviceUnavailable
from app.account_data import AccountDataStore

class RPC:
    def __init__(self,db):self.db=db
    def request(self,path,method,body):
        assert path=='rpc/yingxu_clear_sync_data' and method=='POST'
        try:
            self.db.execute(text('SET LOCAL ROLE service_role'))
            result=self.db.execute(text('SELECT public.yingxu_clear_sync_data(:p_owner,:p_device,:p_origin,:p_request,:p_confirmed)'),body).scalar()
            self.db.commit();return result
        except DBAPIError as exc:
            self.db.rollback()
            code=getattr(exc.orig,'sqlstate',None)
            if code=='PT403':raise DeviceUnavailable() from None
            if code=='PT409':raise StoreConflict() from None
            if code=='PT422':raise ValueError('invalid cleanup') from None
            raise
        except Exception:
            self.db.rollback();raise

class Fixture:
    rpc=False
    def setUp(self):
        self.engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        if not str(self.engine.url.query.get('host','')).startswith('/tmp/yingxu-pg-'):
            self.engine.dispose();raise RuntimeError('Only validator isolated socket PG allowed')
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.principal=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.cleanup=AccountDataStore(RPC(self.db) if self.rpc else self.store,self.principal)
        self.seed('owner');self.seed('other')
    def tearDown(self):
        self.db.close()
        with self.engine.begin() as db:
            for model in reversed(cases.TABLES):
                db.execute(text('DELETE FROM '+model.__tablename__+" WHERE owner_id IN ('owner','other')"))
            db.execute(text("DELETE FROM sync_owner_locks WHERE owner_id IN ('owner','other')"))
        self.engine.dispose()

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class NativeCleanup(Fixture,cases.AccountData):pass

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class RPCCleanup(Fixture,cases.AccountData):rpc=True
