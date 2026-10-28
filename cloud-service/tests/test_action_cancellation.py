"""Cancellation is an owner/device-bound terminal receipt, never cloud execution."""
from datetime import timedelta
import unittest
import test_graph_checkpoint_http as fixtures
from app.main import app
from app.models import AgentAction,utcnow

class ActionCancellation(unittest.TestCase):
    setUp=fixtures.GraphCheckpointHTTP.setUp
    tearDown=fixtures.GraphCheckpointHTTP.tearDown
    def test_cancel_is_terminal_idempotent_and_does_not_authorize_execution(self):
        with app.state.session_factory.begin() as db:
            for state in ('awaiting_local_confirmation','queued','running','proposed'):
                db.add(AgentAction(id=state,owner_id='local-dev-owner',device_id='local-dev-device',origin='desktop',tool='rename_item',state=state,proposal_json={},expires_at=utcnow()-timedelta(days=1)))
        for state in ('awaiting_local_confirmation','queued','running','proposed'):
            with self.subTest(state=state):
                url='/v1/agent/actions/'+state+'/receipt'
                payload={'state':'cancelled','resultCode':'USER_CANCELLED'}
                first=self.client.post(url,json=payload,headers=self.headers)
                self.assertEqual(first.status_code,200,first.text)
                self.assertEqual(first.json()['state'],'cancelled');self.assertFalse(first.json()['executionAuthorized'])
                self.assertTrue(self.client.post(url,json=payload,headers=self.headers).json()['duplicate'])
                self.assertEqual(self.client.post(url,json={**payload,'resultCode':'different'},headers=self.headers).status_code,409)
                self.assertEqual(self.client.post(url,json={'state':'running'},headers=self.headers).status_code,409)
                self.assertEqual(self.client.post(url,json={'state':'succeeded'},headers=self.headers).status_code,409)
