"""Local-only destructive-operation contracts, all data is temporary synthetic data."""
import unittest
import test_graph_checkpoint_http as fixtures

class DataCleanupHTTP(unittest.TestCase):
    setUp=fixtures.GraphCheckpointHTTP.setUp
    tearDown=fixtures.GraphCheckpointHTTP.tearDown
    post=fixtures.GraphCheckpointHTTP.post
    def clear(self,**changes):
        return self.client.request('DELETE','/v1/account/sync-data',json={'requestId':'request','confirmed':True,**changes},headers=self.headers)
    def test_explicit_clear_then_exact_retry_and_old_session_denial(self):
        self.assertEqual(self.post().status_code,200)
        response=self.clear();self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['cleared']);self.assertFalse(response.json()['accountDeleted']);self.assertFalse(response.json()['originalsTouched'])
        self.assertEqual(self.clear().json(),response.json())
        self.assertEqual(self.post(resume=True).status_code,403)
        self.assertEqual(self.client.post('/v1/devices/register',json={'displayName':'Resurrect'},headers=self.headers).status_code,403)
        self.assertEqual(self.clear(requestId='new-request').status_code,403)
    def test_no_confirmation_or_owner_injection_has_no_side_effect(self):
        for changes in ({'confirmed':False},{'confirmed':1},{'ownerId':'other'},{'requestId':''}):
            response=self.clear(**changes);self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.client.get('/v1/devices',headers=self.headers).status_code,200)
