import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_principal, Principal
from app.models import utcnow


class ServiceFlow(unittest.TestCase):
    def test_sync_proposal_isolation_and_websocket_after_store_refactor(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'APP_ENV': 'development', 'AUTH_MODE': 'dev',
            'DEV_AUTH_TOKEN': 'isolated-flow-only', 'DATABASE_URL': 'sqlite:///'+tmp+'/flow.db',
        }):
            with TestClient(app) as client:
                headers = {'Authorization': 'Bearer isolated-flow-only'}
                self.assertEqual(client.get('/readyz').status_code, 200)
                self.assertEqual(client.get('/v1/devices').status_code, 401)
                self.assertEqual(client.post('/v1/devices/register', json={'displayName':'Fixture'}, headers=headers).status_code, 200)
                for invalid_cursor in (-1,2**63):
                    response=client.get('/v1/sync/snapshot',params={'after_id':invalid_cursor},headers=headers)
                    self.assertEqual(response.status_code,422,response.text)
                self.assertEqual(client.get('/v1/sync/snapshot',params={'after_id':2**63-1},headers=headers).json()['nextCursor'],2**63-1)
                from starlette.websockets import WebSocketDisconnect
                for invalid_cursor in (-1,2**63,True,'1'):
                    with client.websocket_connect('/v1/ws') as ws:
                        self.assertEqual(ws.receive_json()['type'],'auth.required')
                        ws.send_json({'token':'isolated-flow-only','afterCursor':invalid_cursor})
                        with self.assertRaises(WebSocketDisconnect) as closed:
                            ws.receive_json()
                        self.assertEqual(closed.exception.code,4400)
                default_preferences={'syncRecords':True,'syncAgentSessions':True,'syncThumbnails':False,'syncPaths':False}
                self.assertEqual(client.get('/v1/account/sync-preferences',headers=headers).json(),default_preferences)
                changed_preferences={**default_preferences,'syncThumbnails':True}
                self.assertEqual(client.put('/v1/account/sync-preferences',json=changed_preferences,headers=headers).status_code,200)
                self.assertEqual(client.get('/v1/account/sync-preferences',headers=headers).json(),changed_preferences)
                invalidations=client.get('/v1/sync/invalidations',headers=headers).json()
                self.assertEqual([r['kind'] for r in invalidations['invalidations']],['paths_redacted'])
                self.assertEqual(client.get('/v1/sync/invalidations',params={'after_id':invalidations['nextCursor']},headers=headers).json()['invalidations'],[])
                event = {'schemaVersion':1,'eventId':'flow-event','deviceId':'local-dev-device',
                    'sequence':1,'eventType':'job.snapshot.v1','occurredAt':utcnow().isoformat(),
                    'payload':{'jobId':'fixture-job','name':'Fixture backup','state':'running','phase':'copy',
                        'files':1,'bytes':2,'progress':25,'targets':[]}}
                bad={**event,'eventId':'invalid','payload':{}}
                self.assertEqual(client.post('/v1/sync/events',json={'events':[event,bad]},headers=headers).status_code,422)
                self.assertEqual(client.get('/v1/sync/snapshot',headers=headers).json()['events'],[])
                collision={**event,'eventId':'conflicting-same-sequence'}
                self.assertEqual(client.post('/v1/sync/events',json={'events':[event,collision]},headers=headers).status_code,409)
                self.assertEqual(client.get('/v1/sync/snapshot',headers=headers).json()['events'],[])
                response = client.post('/v1/sync/events',json={'events':[event]},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['acceptedEventIds'],['flow-event'])
                response = client.post('/v1/sync/events',json={'events':[event]},headers=headers)
                self.assertEqual(response.json()['duplicateEventIds'],['flow-event'])
                conflict = {**event,'payload':{**event['payload'],'progress':50}}
                self.assertEqual(client.post('/v1/sync/events',json={'events':[conflict]},headers=headers).status_code,409)
                snapshot = client.get('/v1/sync/snapshot',headers=headers).json()
                self.assertEqual(len(snapshot['events']),1)
                self.assertEqual(client.get('/v1/sync/snapshot',params={'after_id':snapshot['nextCursor']},headers=headers).json()['events'],[])
                self.assertEqual(len(client.post('/v1/agent/turns',json={'query':'Fixture'},headers=headers).json()['matches']),1)
                for sequence,state in ((3,'success'),(2,'running')):
                    newer={**event,'eventId':'flow-event-'+str(sequence),'sequence':sequence,'payload':{**event['payload'],'state':state}}
                    self.assertEqual(client.post('/v1/sync/events',json={'events':[newer]},headers=headers).status_code,200)
                found=client.post('/v1/agent/turns',json={'query':'Fixture'},headers=headers).json()
                self.assertEqual(len(found['matches']),1)
                self.assertEqual(found['matches'][0]['kind'],'job')
                self.assertEqual(found['matches'][0]['state'],'success')
                self.assertEqual(found['matches'][0]['sequence'],3)
                stored_request={'query':'Fixture','sessionId':'flow-session','turnId':'flow-turn'}
                first_turn=client.post('/v1/agent/turns',json=stored_request,headers=headers)
                self.assertEqual(first_turn.status_code,200,first_turn.text)
                self.assertTrue(first_turn.json()['persisted'])
                self.assertTrue(client.post('/v1/agent/turns',json=stored_request,headers=headers).json()['duplicate'])
                self.assertEqual(client.post('/v1/agent/turns',json={**stored_request,'query':'Changed'},headers=headers).status_code,409)
                self.assertEqual(client.post('/v1/agent/turns',json={'query':'Fixture','sessionId':'incomplete'},headers=headers).status_code,422)
                history=client.get('/v1/agent/sessions/flow-session/turns',headers=headers).json()
                self.assertEqual(len(history['turns']),1)
                self.assertEqual(history['turns'][0]['query'],'Fixture')
                self.assertEqual(client.get('/v1/agent/sessions/flow-session/turns',params={'after_id':history['nextCursor']},headers=headers).json()['turns'],[])
                self.assertEqual(client.request('DELETE','/v1/agent/sessions/flow-session',json={'confirmed':False},headers=headers).status_code,422)
                self.assertEqual(len(client.get('/v1/agent/sessions/flow-session/turns',headers=headers).json()['turns']),1)
                self.assertEqual(client.request('DELETE','/v1/agent/sessions/flow-session',json={'confirmed':True},headers=headers).status_code,200)
                self.assertEqual(client.get('/v1/agent/sessions/flow-session/turns',headers=headers).json()['turns'],[])
                self.assertEqual(client.post('/v1/agent/turns',json=stored_request,headers=headers).status_code,410)
                notices=client.get('/v1/agent/session-deletions',headers=headers).json()
                self.assertEqual([r['sessionId'] for r in notices['deletions']],['flow-session'])
                self.assertEqual(client.get('/v1/agent/session-deletions',params={'after_id':notices['nextCursor']},headers=headers).json()['deletions'],[])
                self.assertEqual(client.get('/v1/agent/tools',headers=headers).json()['orchestrator'],'langgraph')
                lookup=client.post('/v1/agent/tools/get_backup_job',json={'arguments':{'jobId':'fixture-job'}},headers=headers)
                self.assertEqual(lookup.status_code,200,lookup.text)
                self.assertTrue(lookup.json()['result']['found'])
                self.assertFalse(lookup.json()['executionAuthorized'])
                self.assertEqual(client.post('/v1/agent/tools/rename_item',json={'arguments':{}},headers=headers).status_code,403)
                self.assertEqual(client.post('/v1/agent/tools/search_backup_jobs',json={'arguments':{'query':'Fixture','owner':'other'}},headers=headers).status_code,422)
                proposal = {'actionId':'flow-action','tool':'rename_item','fileIds':['file-fixture'],
                    'requestedName':'reviewed-name','expiresAt':(utcnow()+timedelta(minutes=5)).isoformat()}
                response = client.post('/v1/agent/actions',json=proposal,headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                self.assertFalse(response.json()['executionAuthorized'])
                self.assertTrue(client.post('/v1/agent/actions',json=proposal,headers=headers).json()['duplicate'])
                changed={**proposal,'requestedName':'different-name'}
                self.assertEqual(client.post('/v1/agent/actions',json=changed,headers=headers).status_code,409)
                self.assertEqual(client.post('/v1/agent/actions/flow-action/receipt',json={'state':'queued'},headers=headers).status_code,200)
                with patch('app.main.utcnow', return_value=utcnow()+timedelta(hours=1)):
                    for state in ('running','succeeded'):
                        receipt={'state':state,'resultCode':'VERIFIED' if state=='succeeded' else None}
                        response=client.post('/v1/agent/actions/flow-action/receipt',json=receipt,headers=headers)
                        self.assertEqual(response.status_code,200,response.text)
                        retry=client.post('/v1/agent/actions/flow-action/receipt',json=receipt,headers=headers)
                        self.assertEqual(retry.status_code,200,retry.text)
                        self.assertTrue(retry.json()['duplicate'])
                    self.assertEqual(client.post('/v1/agent/actions',json=proposal,headers=headers).status_code,200)
                saved=client.get('/v1/agent/actions/flow-action',headers=headers).json()
                self.assertEqual(saved['resultCode'],'VERIFIED')
                self.assertEqual(saved['state'],'succeeded')
                self.assertEqual(client.post('/v1/agent/actions/flow-action/receipt',json={'state':'succeeded','resultCode':'DIFFERENT'},headers=headers).status_code,409)
                self.assertEqual(client.post('/v1/agent/actions/flow-action/receipt',json={'state':'queued'},headers=headers).status_code,409)
                with client.websocket_connect('/v1/ws') as ws:
                    self.assertEqual(ws.receive_json()['type'],'auth.required')
                    ws.send_json({'token':'isolated-flow-only'})
                    for _ in range(3):
                        self.assertEqual(ws.receive_json()['type'],'sync.event')
                    self.assertEqual(ws.receive_json()['type'],'sync.snapshot.complete')
                app.dependency_overrides[require_principal] = lambda: Principal('second-fixture-owner','second-device','mobile')
                try:
                    self.assertEqual(client.post('/v1/devices/register',json={'displayName':'Mobile fixture'}).status_code,200)
                    self.assertEqual(client.get('/v1/sync/snapshot').json()['events'],[])
                    self.assertEqual(client.get('/v1/account/sync-preferences').json(),default_preferences)
                    self.assertEqual(client.get('/v1/agent/sessions/flow-session/turns').json()['turns'],[])
                    self.assertEqual(client.get('/v1/agent/session-deletions').json()['deletions'],[])
                    self.assertEqual(client.get('/v1/sync/invalidations').json()['invalidations'],[])
                    self.assertEqual(client.post('/v1/agent/actions',json=proposal).status_code,403)
                finally:
                    app.dependency_overrides.clear()

    def test_production_missing_configuration_stays_closed(self):
        with patch.dict(os.environ, {'APP_ENV':'production','AUTH_MODE':'jwt','DATABASE_URL':'',
            'CLOUDBASE_ENV_ID':'','CLOUDBASE_APIKEY':'','JWT_ISSUER':'','JWT_AUDIENCE':'','JWT_JWKS_URL':''}):
            with TestClient(app) as client:
                self.assertEqual(client.get('/healthz').status_code,200)
                result=client.get('/readyz')
                self.assertEqual(result.status_code,503)
                self.assertEqual(result.json()['authentication'],'not_configured')
                self.assertEqual(client.get('/v1/devices',headers={'Authorization':'Bearer invalid'}).status_code,503)

    def test_cloudbase_session_signing_configuration_counts_as_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(
                Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()
            env = {
                'APP_ENV':'development','AUTH_MODE':'cloudbase','DATABASE_URL':'sqlite:///'+tmp+'/cloudbase-ready.db',
                'CLOUDBASE_ENV_ID':'env-fixture','CLOUDBASE_CLIENT_ORIGINS':'{"desktop-client":"desktop"}',
                'SESSION_PRIVATE_KEY':private,'SESSION_KEY_ID':'fixture-key',
                'JWT_ISSUER':'https://issuer.invalid','JWT_AUDIENCE':'yingxu-session',
            }
            with patch.dict(os.environ,env):
                with TestClient(app) as client:
                    result=client.get('/readyz')
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertEqual(result.json()['authentication'],'configured')
                    self.assertEqual(result.json()['database'],'ready')
                    self.assertEqual(result.json()['configurationErrors'],[])

    def test_cloudbase_invalid_signing_configuration_stays_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                'APP_ENV':'development','AUTH_MODE':'cloudbase','DATABASE_URL':'sqlite:///'+tmp+'/cloudbase-not-ready.db',
                'CLOUDBASE_ENV_ID':'env-fixture','CLOUDBASE_CLIENT_ORIGINS':'{"desktop-client":"desktop"}',
                'SESSION_PRIVATE_KEY':'invalid','SESSION_KEY_ID':'fixture-key',
                'JWT_ISSUER':'https://issuer.invalid','JWT_AUDIENCE':'yingxu-session',
            }
            with patch.dict(os.environ,env):
                with TestClient(app) as client:
                    result=client.get('/readyz')
                    self.assertEqual(result.status_code,503,result.text)
                    self.assertEqual(result.json()['authentication'],'not_configured')
                    self.assertIn('cloudbase auth requires valid session signing configuration',result.json()['configurationErrors'])
