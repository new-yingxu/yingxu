"""Offline real LangGraph resume tests; no provider/network credentials."""
from contextlib import contextmanager
from typing import TypedDict
import unittest
import tempfile
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.base import empty_checkpoint
from app.db import Base
from app.models import Device, SyncPreferences, DeletedAgentSession
from app.store import SQLStore, SyncForbidden, DeviceUnavailable, SessionDeleted
from app.graph_checkpoints import PrincipalCheckpointSaver, GraphCheckpoint, GraphPendingWrite


def setup(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'graph.sqlite'),connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    with sessions.begin() as db:
        db.add_all([Device(owner_id='owner',device_id='device',origin='desktop'),Device(owner_id='other',device_id='device',origin='desktop')])
    @contextmanager
    def factory():
        with sessions() as db: yield SQLStore(db)
    def saver(owner='owner',thread='server-thread'):
        return PrincipalCheckpointSaver(factory,owner_id=owner,device_id='device',origin='desktop',session_id='session',thread_id=thread)
    yield saver,sessions
    engine.dispose()


CONFIG={'configurable':{'thread_id':'server-thread'}}


def _test_interrupt_new_saver_resume(setup):
    saver,_=setup
    class State(TypedDict): value:int
    def approval(state): return {'value':state['value']+interrupt('approve')}
    def build(cp):
        graph=StateGraph(State);graph.add_node('approval',approval);graph.add_edge(START,'approval');graph.add_edge('approval',END)
        return graph.compile(checkpointer=cp)
    assert build(saver()).invoke({'value':2},CONFIG)['__interrupt__']
    stored=saver().get_tuple(CONFIG)
    assert stored.parent_config and any(w[1]=='__interrupt__' for w in stored.pending_writes)
    assert build(saver()).invoke(Command(resume=5),CONFIG)['value']==7
    assert len(list(saver().list(CONFIG)))>=3


def _test_failure_pending_write_recovery(setup):
    saver,_=setup
    calls=[]
    class State(TypedDict,total=False): good:int; bad:int
    def good(state): calls.append('good');return {'good':1}
    def bad(state):
        if calls.count('bad')==0: calls.append('bad');raise RuntimeError('planned')
        return {'bad':2}
    def build(cp):
        graph=StateGraph(State)
        graph.add_node('good',good);graph.add_node('bad',bad)
        graph.add_edge(START,'good');graph.add_edge(START,'bad');graph.add_edge('good',END);graph.add_edge('bad',END)
        return graph.compile(checkpointer=cp)
    with unittest.TestCase().assertRaisesRegex(RuntimeError,'planned'): build(saver()).invoke({},CONFIG)
    writes=saver().get_tuple(CONFIG).pending_writes
    assert any(w[1]=='good' for w in writes) and any(w[1]=='__error__' for w in writes)
    assert build(saver()).invoke(None,CONFIG)=={'good':1,'bad':2}
    assert calls.count('good')==1


def _test_isolation_preferences_deletion_revocation(setup):
    saver,sessions=setup
    cp=empty_checkpoint(); config=saver().put(CONFIG,cp,{'source':'input','step':-1,'parents':{}},{})
    assert saver('other').get_tuple(CONFIG) is None
    assert saver(thread='different').get_tuple({'configurable':{'thread_id':'different'}}) is None
    with unittest.TestCase().assertRaises(ValueError): saver().get_tuple({'configurable':{'thread_id':'different'}})
    with sessions.begin() as db: db.add(SyncPreferences(owner_id='owner',sync_agent_sessions=False))
    for operation in [lambda:saver().get_tuple(CONFIG),lambda:saver().put(CONFIG,empty_checkpoint(),{},{}),lambda:saver().put_writes(config,[('x',1)],'task')]:
        operation()
    assert saver().get_tuple(CONFIG) is None
    assert list(saver().list(CONFIG)) == []
    with sessions.begin() as db:db.get(SyncPreferences,'owner').sync_agent_sessions=True;db.add(DeletedAgentSession(owner_id='owner',session_id='session'))
    with unittest.TestCase().assertRaises(SessionDeleted):saver().get_tuple(CONFIG)
    with sessions.begin() as db:
        db.query(DeletedAgentSession).delete();db.scalar(select(Device).where(Device.owner_id=='owner')).revoked=True
    with unittest.TestCase().assertRaises(DeviceUnavailable):saver().get_tuple(CONFIG)


def _test_negative_indexes_and_no_credentials(setup):
    saver,sessions=setup
    cp=empty_checkpoint(); config=saver().put(CONFIG,cp,{},{} )
    saver().put_writes(config,[('__error__','first'),('value',1)],'task')
    saver().put_writes(config,[('__error__','second'),('value',2)],'task')
    writes=saver().get_tuple(config).pending_writes
    assert ('task','__error__','second') in writes and ('task','value',1) in writes
    cp['channel_values']={'access_token':'DO-NOT-SAVE'}
    with unittest.TestCase().assertRaisesRegex(ValueError,'Credentials'):saver().put(CONFIG,cp,{},{} )
    with sessions() as db:
        assert 'DO-NOT-SAVE' not in str(db.scalars(select(GraphCheckpoint.payload)).all())
    saver().delete_thread('server-thread')
    assert saver().get_tuple(CONFIG) is None
    with sessions() as db: assert not db.scalars(select(GraphPendingWrite)).all()


def _test_midrun_optout(setup):
    saver,sessions=setup
    class State(TypedDict): value:int
    def optout(state):
        with sessions.begin() as db: db.add(SyncPreferences(owner_id='owner',sync_agent_sessions=False))
        return {'value':state['value']+1}
    graph=StateGraph(State);graph.add_node('optout',optout);graph.add_edge(START,'optout');graph.add_edge('optout',END)
    assert graph.compile(checkpointer=saver()).invoke({'value':2},CONFIG)['value']==3
    assert saver().get_tuple(CONFIG) is None
    with sessions() as db:
        stored=db.scalars(select(GraphCheckpoint)).all()
        assert all('optout' not in str(row.payload) for row in stored)


def _test_wrapper_resume_and_delete(setup):
    from app.agent_graph import run_turn_graph
    saver,sessions=setup
    calls=[]
    def retrieve(): calls.append('retrieve');return {'answer':'initial'}
    def model(response):
        approved=interrupt('approval')
        calls.append('model')
        return {'answer':approved}
    initial=run_turn_graph(retrieve,model,lambda response:response,True,checkpointer=saver(),thread_id='server-thread')
    assert initial['__interrupt__']
    result=run_turn_graph(retrieve,model,lambda response:response,True,checkpointer=saver(),thread_id='server-thread',resume=Command(resume='approved'))
    assert result['answer']=='approved' and calls==['retrieve','model']
    # A second owner shares the textual session/thread identifiers but survives deletion.
    saver('other').put(CONFIG,empty_checkpoint(),{},{} )
    with sessions() as db: SQLStore(db).delete_session('owner','device','session')
    with sessions() as db:
        assert not db.scalars(select(GraphCheckpoint).where(GraphCheckpoint.owner_id=='owner')).all()
        assert not db.scalars(select(GraphPendingWrite).where(GraphPendingWrite.owner_id=='owner')).all()
        assert db.scalar(select(DeletedAgentSession).where(DeletedAgentSession.owner_id=='owner')) is not None
    assert saver('other').get_tuple(CONFIG) is not None
    with unittest.TestCase().assertRaises(SessionDeleted):saver().get_tuple(CONFIG)


def _test_wrapper_failed_resume(setup):
    from app.agent_graph import run_turn_graph
    saver,_=setup
    calls=[]
    def retrieve(): calls.append('retrieve');return {'answer':'initial'}
    def persist(response):
        if 'failed' not in calls: calls.append('failed');raise RuntimeError('planned wrapper failure')
        return dict(response,persisted=True)
    with unittest.TestCase().assertRaisesRegex(RuntimeError,'planned wrapper failure'):
        run_turn_graph(retrieve,lambda response:response,persist,False,checkpointer=saver(),thread_id='server-thread')
    result=run_turn_graph(retrieve,lambda response:response,persist,False,checkpointer=saver(),thread_id='server-thread',resume=True)
    assert result['persisted'] and calls.count('retrieve')==1


class TestGraphCheckpointTests(unittest.TestCase):
    def test_wrapper_resume_and_delete(self): _test_wrapper_resume_and_delete(self.context)
    def test_wrapper_failed_resume(self): _test_wrapper_failed_resume(self.context)
    def test_midrun_optout(self): _test_midrun_optout(self.context)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.fixture=setup(Path(self.temp.name))
        self.context=next(self.fixture)
    def tearDown(self):
        try: next(self.fixture)
        except StopIteration: pass
        self.temp.cleanup()
    def test_interrupt_new_saver_resume(self): _test_interrupt_new_saver_resume(self.context)
    def test_failure_pending_write_recovery(self): _test_failure_pending_write_recovery(self.context)
    def test_isolation_preferences_deletion_revocation(self): _test_isolation_preferences_deletion_revocation(self.context)
    def test_negative_indexes_and_no_credentials(self): _test_negative_indexes_and_no_credentials(self.context)

if __name__=="__main__": unittest.main()
