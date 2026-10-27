"""Explicit opt-in integration with an already installed LOCAL Ollama model.

No model download, cloud call, original media, paid key or service restart.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.models import utcnow
from app.model_provider import generate_answer,_tool_provider_request
from app.agent_tools import ReadOnlyToolRegistry


@unittest.skipUnless(os.getenv('YINGXU_RUN_OLLAMA_TESTS')=='1','Actual local Ollama is opt-in')
class OllamaIntegration(unittest.TestCase):
    def test_actual_langgraph_response_grounding_and_deduplicated_replay(self):
        model=os.getenv('YINGXU_OLLAMA_MODEL','qwen2.5:7b')
        token=secrets.token_urlsafe(32)
        credential=json.dumps({'local-dev-owner':{'provider':'ollama','model':model}})
        with tempfile.TemporaryDirectory(prefix='yingxu-ollama-test-') as tmp,patch.dict(os.environ,{'APP_ENV':'development','AUTH_MODE':'dev','DEV_AUTH_TOKEN':token,'DATABASE_URL':'sqlite:///'+tmp+'/test.db','MODEL_OWNER_CREDENTIALS':credential}),patch('app.main.generate_answer',wraps=generate_answer) as provider,patch('app.model_provider._tool_provider_request',wraps=_tool_provider_request) as wire,patch.object(ReadOnlyToolRegistry,'invoke',autospec=True,side_effect=ReadOnlyToolRegistry.invoke) as tools,TestClient(app) as client:
            headers={'Authorization':'Bearer '+token}
            self.assertEqual(client.post('/v1/devices/register',json={'displayName':'Synthetic Ollama fixture'},headers=headers).status_code,200)
            event={'schemaVersion':1,'eventId':'local-event','deviceId':'local-dev-device','sequence':1,'eventType':'job.snapshot.v1','occurredAt':utcnow().isoformat(),'payload':{'jobId':'local-job','name':'Ollama本地隔离任务','state':'running','phase':'copy','files':3,'bytes':8,'progress':25,'targets':[]}}
            self.assertEqual(client.post('/v1/sync/events',json={'events':[event]},headers=headers).status_code,200)
            request={'query':'Ollama本地隔离任务','useModel':True,'sessionId':'local-model','turnId':'first'}
            response=client.post('/v1/agent/turns',json=request,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            result=response.json()
            self.assertGreaterEqual(wire.call_count,2,'actual model must request a tool and consume its result')
            self.assertGreaterEqual(tools.call_count,2,'initial retrieval plus at least one model-selected tool')
            wire_count=wire.call_count
            self.assertEqual(result['provider'],'ollama')
            self.assertEqual(result['orchestrator'],'langgraph')
            self.assertFalse(result['executionAuthorized'])
            self.assertTrue(result['answer'].strip())
            self.assertEqual(result['matches'][0]['state'],'running')
            self.assertGreater(result['usage'].get('total_tokens',0),0)
            repeat=client.post('/v1/agent/turns',json=request,headers=headers)
            self.assertEqual(repeat.status_code,200,repeat.text)
            self.assertTrue(repeat.json()['duplicate'])
            self.assertEqual(repeat.json()['answer'],result['answer'])
            resumed=client.post('/v1/agent/turns',json={**request,'resume':True},headers=headers)
            self.assertEqual(resumed.status_code,200,resumed.text)
            self.assertTrue(resumed.json()['duplicate'])
            self.assertEqual(resumed.json()['answer'],result['answer'])
            self.assertEqual(provider.call_count,1)
            self.assertEqual(wire.call_count,wire_count)
