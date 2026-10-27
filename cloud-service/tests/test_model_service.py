import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.model_provider import ModelUnavailable


class ModelService(unittest.TestCase):
    def test_opt_in_retry_uncertainty_and_history_opt_out(self):
        secret=json.dumps({'local-dev-owner':{'apiKey':'synthetic-model-key'}})
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'APP_ENV':'development','AUTH_MODE':'dev','DEV_AUTH_TOKEN':'local-fixture-only','DATABASE_URL':'sqlite:///'+tmp+'/test.db','MODEL_OWNER_CREDENTIALS':secret}),TestClient(app) as client:
            headers={'Authorization':'Bearer local-fixture-only'}
            self.assertEqual(client.post('/v1/devices/register',json={'displayName':'Fixture'},headers=headers).status_code,200)
            status=client.get('/v1/agent/model',headers=headers)
            self.assertTrue(status.json()['configured'])
            self.assertNotIn('synthetic-model-key',status.text)
            request={'query':'Fixture','sessionId':'model','turnId':'one','useModel':True}
            result={'answer':'Synthetic provider result','model':'qwen-plus','provider':'dashscope','usage':{'total_tokens':4},'executionAuthorized':False}
            with patch('app.main.generate_answer',return_value=result) as provider:
                plain=client.post('/v1/agent/turns',json={'query':'Fixture'},headers=headers)
                self.assertIsNone(plain.json()['model'])
                provider.assert_not_called()
                first=client.post('/v1/agent/turns',json=request,headers=headers)
                self.assertEqual(first.status_code,200,first.text)
                self.assertTrue(first.json()['persisted'])
                repeat=client.post('/v1/agent/turns',json=request,headers=headers)
                self.assertTrue(repeat.json()['duplicate'])
                self.assertEqual(repeat.json()['answer'],first.json()['answer'])
                self.assertEqual(provider.call_count,1)
                self.assertEqual(client.post('/v1/agent/turns',json={**request,'query':'Changed'},headers=headers).status_code,409)
                self.assertEqual(provider.call_count,1)
                self.assertEqual(client.post('/v1/agent/turns',json={'query':'Fixture','useModel':True},headers=headers).status_code,422)
                client.put('/v1/account/sync-preferences',json={'syncRecords':True,'syncAgentSessions':False,'syncThumbnails':False,'syncPaths':False},headers=headers)
                private={**request,'turnId':'private'}
                answer=client.post('/v1/agent/turns',json=private,headers=headers)
                self.assertEqual(answer.status_code,200,answer.text)
                self.assertFalse(answer.json()['persisted'])
                self.assertEqual(provider.call_count,2)
                self.assertEqual(client.post('/v1/agent/turns',json=private,headers=headers).json()['detail']['code'],'MODEL_REQUEST_COMPLETED_UNRETAINED')
                self.assertEqual(provider.call_count,2)
            with patch('app.main.generate_answer',side_effect=ModelUnavailable('synthetic failure')) as provider:
                uncertain={**request,'turnId':'uncertain'}
                self.assertEqual(client.post('/v1/agent/turns',json=uncertain,headers=headers).status_code,502)
                self.assertEqual(client.post('/v1/agent/turns',json=uncertain,headers=headers).json()['detail']['code'],'MODEL_REQUEST_UNCERTAIN')
                self.assertEqual(provider.call_count,1)
            with patch('app.main.generate_answer') as provider:
                app.state.settings=__import__('dataclasses').replace(app.state.settings,model_owner_credentials=None)
                self.assertEqual(client.post('/v1/agent/turns',json={**request,'turnId':'unconfigured'},headers=headers).status_code,503)
                self.assertFalse(client.get('/v1/agent/model',headers=headers).json()['configured'])
                provider.assert_not_called()
