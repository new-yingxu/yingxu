import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.agent_graph import build_turn_graph,run_turn_graph
from app.agent_tools import ReadOnlyToolRegistry,ToolNotAllowed,tool_catalog
from app.auth import Principal
from langsmith.utils import tracing_is_enabled
from pydantic import ValidationError


class AgentGraph(unittest.TestCase):
    def test_actual_graph_routes_readonly_and_model_separately(self):
        retrieve=Mock(return_value={'answer':'summary','model':None})
        model=Mock(return_value={'answer':'model answer','model':'fixture'})
        persist=Mock(side_effect=lambda response:{**response,'persisted':True})
        result=run_turn_graph(retrieve,model,persist,False)
        self.assertEqual(result['orchestrator'],'langgraph')
        self.assertTrue(result['persisted'])
        model.assert_not_called()
        retrieve.reset_mock();persist.reset_mock()
        result=run_turn_graph(retrieve,model,persist,True)
        self.assertEqual(result['model'],'fixture')
        model.assert_called_once()
        persist.assert_not_called()  # Model callback owns reserved completion.
        nodes=build_turn_graph(retrieve,model,persist).get_graph().nodes
        self.assertIn('reserved_model_call',nodes)

    def test_model_node_never_retries_and_tracing_stays_disabled(self):
        observed=[]
        def retrieve():
            observed.append(tracing_is_enabled())
            return {'answer':'summary'}
        model=Mock(side_effect=RuntimeError('fixture failure'))
        persist=Mock()
        with patch.dict(os.environ,{'LANGSMITH_TRACING':'true'}),self.assertRaises(RuntimeError):
            run_turn_graph(retrieve,model,persist,True)
        self.assertEqual(observed,[False])
        self.assertEqual(model.call_count,1)
        persist.assert_not_called()

    def test_tool_adapter_binds_owner_and_rejects_writes_or_owner_injection(self):
        store=Mock()
        store.events.return_value=[SimpleNamespace(device_id='device',sequence=1,body_json={'payload':{'jobId':'job','name':'fixture','state':'success','paths':['private']},'occurredAt':'time'})]
        tools=ReadOnlyToolRegistry(store,Principal('owner','device','desktop'))
        result=tools.invoke('search_backup_jobs',{'query':'fixture'})
        store.events.assert_called_once_with('owner',limit=200,newest=True)
        self.assertEqual(result['matches'][0]['kind'],'job')
        self.assertNotIn('private',str(result))
        with self.assertRaises(ValidationError):tools.invoke('search_backup_jobs',{'query':'fixture','owner':'other'})
        for name in ('rename_item','move_item','shell','unknown'):
            with self.assertRaises(ToolNotAllowed):tools.invoke(name,{'query':'fixture'})
        self.assertFalse(any(t['executableInCloud'] for t in tool_catalog() if t['permission']=='proposal_only'))
