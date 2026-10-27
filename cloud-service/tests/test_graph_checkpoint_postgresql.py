"""Real socket-only PostgreSQL graph resume, native SQL and service-role RPC."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt,Command
from typing import TypedDict
from app.models import Device
from app.config import REQUIRED_SCHEMA_VERSION
from app.store import SQLStore
from app.graph_checkpoints import PrincipalCheckpointSaver

@unittest.skipUnless(os.getenv('YINGXU_TEST_PG_URL'),'Local PostgreSQL is opt-in')
class GraphPostgreSQLTests(unittest.TestCase):
    def test_ready_rejects_previous_schema(self):
        engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        self.addCleanup(engine.dispose)
        self.assertTrue(str(engine.url.query.get('host','')).startswith('/tmp/yingxu-pg-'))
        with Session(engine) as db:
            db.execute(text('DELETE FROM schema_migrations WHERE version=:version'),{'version':REQUIRED_SCHEMA_VERSION})
            self.assertFalse(SQLStore(db).ready(production=True))
            db.rollback()
            self.assertTrue(SQLStore(db).ready(production=True))

    def test_native_and_rpc_resume(self):
        engine=create_engine(os.environ['YINGXU_TEST_PG_URL'])
        self.addCleanup(engine.dispose)
        self.assertTrue(str(engine.url.query.get('host','')).startswith('/tmp/yingxu-pg-'))
        for rpc in (False,True):
            with self.subTest(rpc=rpc):
                owner='checkpoint-test-'+uuid4().hex
                with Session(engine) as db:
                    db.add(Device(owner_id=owner,device_id='device',origin='desktop'));db.commit()
                class Adapter:
                    def __init__(self,db):self.db=db
                    def request(self,path,method,body):
                        assert path=='rpc/yingxu_graph_checkpoint' and method=='POST'
                        self.db.execute(text('SET LOCAL ROLE service_role'))
                        args={**body,'p_payload':json.dumps(body['p_payload'])}
                        sql='SELECT public.yingxu_graph_checkpoint('+','.join('CAST(:'+k+' AS jsonb)' if k=='p_payload' else ':'+k for k in body)+')'
                        result=self.db.execute(text(sql),args).scalar();self.db.commit();return result
                @contextmanager
                def factory():
                    with Session(engine) as db:yield Adapter(db) if rpc else SQLStore(db)
                def saver():return PrincipalCheckpointSaver(factory,owner_id=owner,device_id='device',origin='desktop',session_id='session',thread_id='thread')
                class State(TypedDict):value:int
                def build():
                    graph=StateGraph(State);graph.add_node('approve',lambda state:{'value':state['value']+interrupt('continue')});graph.add_edge(START,'approve');graph.add_edge('approve',END)
                    return graph.compile(checkpointer=saver())
                config={'configurable':{'thread_id':'thread'}}
                self.assertTrue(build().invoke({'value':2},config)['__interrupt__'])
                self.assertEqual(build().invoke(Command(resume=3),config)['value'],5)
                self.assertGreater(len(list(saver().list(config))),1)
                saver().delete_thread('thread')
                self.assertIsNone(saver().get_tuple(config))
                self.assertTrue(build().invoke({'value':7},config)['__interrupt__'])
                with Session(engine) as db:
                    if rpc:
                        db.execute(text('SET LOCAL ROLE service_role'))
                        db.execute(text('SELECT public.yingxu_set_preferences(:owner,true,false,false,false)'),{'owner':owner});db.commit()
                    else:
                        SQLStore(db).set_preferences(owner,dict(sync_records=True,sync_agent_sessions=False,sync_thumbnails=False,sync_paths=False))
                self.assertIsNone(saver().get_tuple(config))
                with engine.begin() as db:
                    self.assertEqual(db.execute(text('SELECT count(*) FROM agent_graph_checkpoints WHERE owner_id=:owner'),{'owner':owner}).scalar(),0)
                    self.assertEqual(db.execute(text('SELECT count(*) FROM agent_graph_pending_writes WHERE owner_id=:owner'),{'owner':owner}).scalar(),0)
                    db.execute(text('UPDATE sync_preferences SET sync_agent_sessions=true WHERE owner_id=:owner'),{'owner':owner})
                self.assertIsNone(saver().get_tuple(config))
                with engine.begin() as db:
                    for table in ('agent_graph_pending_writes','agent_graph_checkpoints','sync_invalidations','sync_preferences','devices','sync_owner_locks'):
                        db.execute(text('DELETE FROM '+table+' WHERE owner_id=:owner'),{'owner':owner})
